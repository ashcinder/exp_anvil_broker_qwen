"""Two real Anvil shards, one broker, paired runs of a strictly one-way trace."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.procmon import RSSWatchdog
from brokerlab.real_data import extract
from experiments.exp025_regime_shift_sensitivity import pipeline
from experiments.exp025_regime_shift_sensitivity.paper_data import export_arm_data, read_csv, write_csv
from experiments.exp025_regime_shift_sensitivity.runner import free_port_block, token_tag, write_json


def build_workload(cfg, out):
    n = int(cfg.exp["ctx_per_broker"])
    src, dst = int(cfg.exp["source_shard"]), int(cfg.exp["destination_shard"])
    if {src, dst} != {0, 1}:
        raise ValueError("Select opposite directions on the two shards")
    limit, maximum = int(cfg.exp["pool_scan"]), int(cfg.exp["pool_scan_max"])
    if not 0 < limit <= maximum:
        raise ValueError("Require 0 < pool_scan <= pool_scan_max")
    while True:
        pool = extract(cfg.traffic.real_csv_path, num_shards=2, num_users=cfg.scale.num_users,
                       user_base_index=cfg.scale.user_base_index,
                       value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                       value_cap_wei=int(cfg.traffic.value_cap_eth * ETH), limit=limit,
                       allow_contract_endpoints=cfg.traffic.allow_contract_endpoints)
        unique = {}
        for row in pool:
            if row.src_shard == src and row.dst_shard == dst:
                unique.setdefault(row.ctx_id, asdict(row))
        print(f"direction {src}->{dst}: {len(unique)} unique eligible CTX; limit={limit}", flush=True)
        if len(unique) >= n or len(pool) < limit or limit >= maximum:
            break
        limit = min(limit * 2, maximum)
    if len(unique) < n:
        raise ValueError(f"Need {n} one-way CTX, found {len(unique)}; supply more data or increase pool_scan_max")
    # Retain original trace order. No destination rewriting or duplicated samples.
    rows = list(unique.values())[:n]
    for pos, row in enumerate(rows):
        row["arrival_pos"] = pos
    # The mechanism demonstration requires divergence, not complete depletion.
    # Keep the actual small amounts even when total demand is below initial funding.
    total_wei = sum(row["amount_wei"] for row in rows)
    if total_wei <= 0:
        raise ValueError("Selected directional transfer demand must be positive")
    write_csv(out/"workload.csv", rows, list(rows[0]))
    write_json(out/"workload_selection.json", dict(direction=[src, dst], selected=n,
               effective_pool_scan=limit, unique_directional_candidates=len(unique),
               source=str(cfg.traffic.real_csv_path), destinations_rewritten=False,
               amounts_rescaled=False, total_transfer_eth=total_wei / ETH,
               value_floor_eth=cfg.traffic.value_floor_eth, value_cap_eth=cfg.traffic.value_cap_eth))
    print(f"Selected real transfer volume: {total_wei / ETH:.6f} ETH; "
          f"destination initial funding: {cfg.exp['balances_eth']} ETH", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE/"config.yaml")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--output-root", type=Path, default=HERE/"out")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), args.set)
    if cfg.chain.num_shards != 2 or cfg.scale.num_brokers != 1 or int(cfg.exp["repetitions"]) != 1:
        raise ValueError("exp027 requires exactly two shards, one broker and one paired round")
    tokens = cfg.exp["arms"].split(",")
    if len(tokens) != 2 or not tokens[0].startswith("plain@") or not tokens[1].startswith("topup@"):
        raise ValueError("Configure Plain followed by Top-up+EWMA")
    funds = [pipeline._arm_spec(token)[2] for token in tokens]
    if any(value != float(cfg.exp["balances_eth"]) for value in funds):
        raise ValueError("Use equal initial funding in both paired arms")
    out = args.output_root.resolve()/datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True)
    write_json(out/"params.json", to_params_dict(cfg))
    workload = build_workload(cfg, out)
    arms = []
    report = dict(experiment="exp027", params=to_params_dict(cfg), status={"complete": False}, arms=arms,
                  workload_sha256=hashlib.sha256((out/"workload.csv").read_bytes()).hexdigest())
    write_json(out/"report.json", report)
    for token in tokens:
        tag = token_tag(token)
        effective = replace(cfg, chain=replace(cfg.chain, base_port=free_port_block(cfg.chain.base_port, 2)))
        watchdog = RSSWatchdog(float(cfg.exp["rss_cap_mb"]))
        watchdog.start()
        try:
            _, engine, fund, cap, eps, chi, hl, q_min, frac = pipeline._arm_spec(token)
            print(f"Running {tag}: {len(workload)} CTX", flush=True)
            stats = pipeline.run_arm(effective, tag, workload, sum(r["amount_wei"] for r in workload),
                                     out, watchdog, engine=engine, fund_eth=fund, cap_mult=cap,
                                     eps=eps, chi=chi, hl=hl, qmin=q_min, frac=frac)
            # Preserve diagnostics even when the experiment is incomplete and exports reject it.
            write_json(out/tag/"execution_summary.json", dict(params=to_params_dict(effective), arms=[stats],
                       status={"complete": False, "validation_pending": True}))
            if stats.get("coord", {}).get("aborted"):
                raise RuntimeError(f"{tag}: coordinator aborted: {stats['coord']['aborted']}; "
                                   f"recorded {stats['n']}/{len(workload)} CTX. "
                                   "See coordinator.json; increase exp.drain_tail_s if it reached its deadline.")
            export_arm_data(effective, stats, out/tag, workload)
            # Because there is exactly ONE broker, this aggregate is its own two sub-accounts.
            balance = read_csv(out/tag/"balance_by_block.csv")
            if engine == "plain":
                gap = [abs(float(r["shard_0_balance_eth"])-float(r["shard_1_balance_eth"])) for r in balance]
                if stats["served"] <= 0 or max(gap) <= 0:
                    raise ValueError("No observed broker sub-account divergence")
                stats["observed_max_subaccount_gap_eth"] = max(gap)
            arms.append(stats)
            write_json(out/tag/"summary.json", dict(params=to_params_dict(effective), arms=[stats]))
            write_json(out/"report.json", report)
        except BaseException as error:
            report["failure"] = dict(arm=tag, error=str(error))
            write_json(out/"report.json", report)
            raise
        finally:
            watchdog.stop()
    report["status"]["complete"] = True
    write_json(out/"report.json", report)
    print(f"Completed exp027 data: {out}", flush=True)
    if not args.no_plot:
        return subprocess.call([sys.executable, str(HERE/"plot.py"), "--source", str(out)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
