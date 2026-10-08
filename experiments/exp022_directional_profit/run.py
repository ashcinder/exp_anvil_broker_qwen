#!/usr/bin/env python3
"""Experiment 22: paired directional drain and nominal broker fee revenue."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.config import ETH, apply_overrides, b2e_fees, load_config, to_params_dict  # noqa: E402
from brokerlab.procmon import RSSWatchdog  # noqa: E402
from experiments.exp003_tdr_on_off.run import run_arm  # noqa: E402


def _wei(value) -> int:
    amount = Decimal(str(value)) * ETH
    if amount != amount.to_integral_value():
        raise ValueError("amount_eth must resolve to an integer number of wei")
    return int(amount)


def make_workload(cfg) -> list[dict]:
    if cfg.chain.num_shards != 2 or cfg.scale.num_brokers != 1:
        raise ValueError("exp022 requires exactly two shards and one broker")
    if cfg.scale.num_users < 2:
        raise ValueError("exp022 needs at least two user identities")
    src, dst = int(cfg.exp["source_shard"]), int(cfg.exp["target_shard"])
    if {src, dst} != {0, 1}:
        raise ValueError("source_shard and target_shard must be 0 and 1 in either order")
    count, value = int(cfg.exp["count"]), _wei(cfg.exp["amount_eth"])
    if count < 1 or value <= 0 or int(cfg.exp["rate"]) < 1:
        raise ValueError("count, amount_eth and rate must be positive")
    if not cfg.b2e.enabled or cfg.b2e.charges_rebalancing:
        raise ValueError("exp022 requires B2E enabled and charges_rebalancing=false")
    if int(cfg.exp["balances_eth"] * ETH) <= 0:
        raise ValueError("balances_eth must be positive")
    half = cfg.scale.num_users // 2
    base = cfg.scale.user_base_index
    return [{"ctx_id": f"exp022_{i:06d}", "src_shard": src, "dst_shard": dst,
             "sender_idx": base + i % half,
             "receiver_idx": base + half + i % (cfg.scale.num_users - half),
             "amount_wei": value, "arrival_pos": i}
            for i in range(count)]


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _block(row: dict, key: str) -> int:
    value = row.get(key)
    if value in (None, "", "None"):
        raise ValueError(f"missing {key} for {row.get('ctx_id', 'TDR event')}")
    return int(value)


def analyze_arm(arm_dir: Path, workload: list[dict], stats: dict,
                initial_wei: int, src: int, dst: int) -> tuple[list[dict], list[dict], dict]:
    rows = _read_csv(arm_dir / "ctx_rows.csv")
    if len(rows) != len(workload) or {r["ctx_id"] for r in rows} != {r["ctx_id"] for r in workload}:
        raise ValueError(f"{arm_dir.name}: user CTX missing or duplicated")
    if any(str(r["ok"]).lower() not in ("true", "1") for r in rows):
        raise ValueError(f"{arm_dir.name}: unsuccessful user CTX")
    if not stats.get("gates") or not all(stats["gates"].values()) or not stats["burn_reconcile"]["ok"]:
        raise ValueError(f"{arm_dir.name}: chain integrity gate failed")
    if stats["tdr"]["transfers_lost"] or stats["tdr"]["lost_in_transit_wei"]:
        raise ValueError(f"{arm_dir.name}: TDR loss requires a separately observed burn-block ledger")

    events = defaultdict(lambda: {"a_delta_wei": 0, "b_delta_wei": 0,
                                "confirmed_ctx": 0, "broker_ctx": 0,
                                "relay_ctx": 0, "fee_wei": 0,
                                "tdr_burn": 0, "tdr_mint": 0})
    total_fee = 0
    first_relay = None
    for row in rows:
        if int(row["src"]) != src or int(row["dst"]) != dst:
            raise ValueError(f"{arm_dir.name}: direction changed")
        value = int(row["amount_wei"])
        t2 = _block(row, "t2_block")
        is_broker = row["route"] == "broker"
        fee = int(row.get("fee_broker_wei") or 0)
        if is_broker:
            t1 = _block(row, "t1_block")
            events[t1]["a_delta_wei"] += value + fee
            events[t2]["b_delta_wei"] -= value
            events[t2]["broker_ctx"] += 1
            events[t2]["fee_wei"] += fee
            total_fee += fee
        elif row["route"] == "relay":
            events[t2]["relay_ctx"] += 1
            first_relay = t2 if first_relay is None else min(first_relay, t2)
            if fee:
                raise ValueError("relay gave broker a fee")
        else:
            raise ValueError(f"unexpected route {row['route']}")
        events[t2]["confirmed_ctx"] += 1

    moves = _read_csv(arm_dir / "tdr_move_events.csv")
    if len(moves) != stats["tdr"]["transfers_done"]:
        raise ValueError(f"{arm_dir.name}: TDR block events do not match successful moves")
    for move in moves:
        amount = int(move["amount_wei"])
        burn, mint = _block(move, "burn_block"), _block(move, "mint_block")
        if burn > mint or {int(move["src_shard"]), int(move["dst_shard"])} != {src, dst}:
            raise ValueError("invalid TDR move")
        from_key = "a_delta_wei" if int(move["src_shard"]) == src else "b_delta_wei"
        to_key = "a_delta_wei" if int(move["dst_shard"]) == src else "b_delta_wei"
        events[burn][from_key] -= amount
        events[mint][to_key] += amount
        events[burn]["tdr_burn"] += 1
        events[mint]["tdr_mint"] += 1

    first, last = min(events), max(events)
    a = b = initial_wei
    n = profit = 0
    balance_rows, profit_rows = [], []
    balance_rows.append({"arm": arm_dir.name, "block": first - 1,
                         "source_balance_wei": a, "target_balance_wei": b,
                         "confirmed_ctx": 0, "broker_ctx": 0, "relay_ctx": 0,
                         "tdr_burn": 0, "tdr_mint": 0})
    profit_rows.append({"arm": arm_dir.name, "block": first - 1,
                        "confirmed_ctx": 0, "cumulative_broker_ctx": 0,
                        "cumulative_relay_ctx": 0, "cumulative_profit_wei": 0})
    served = relay = 0
    for block in range(first, last + 1):
        event = events[block]
        a += event["a_delta_wei"]
        b += event["b_delta_wei"]
        n += event["confirmed_ctx"]
        served += event["broker_ctx"]
        relay += event["relay_ctx"]
        profit += event["fee_wei"]
        if min(a, b) < 0:
            raise ValueError(f"{arm_dir.name}: reconstructed negative broker balance at block {block}")
        balance_rows.append({"arm": arm_dir.name, "block": block,
                             "source_balance_wei": a, "target_balance_wei": b,
                             "confirmed_ctx": n, "broker_ctx": served,
                             "relay_ctx": relay, "tdr_burn": event["tdr_burn"],
                             "tdr_mint": event["tdr_mint"]})
        profit_rows.append({"arm": arm_dir.name, "block": block,
                            "confirmed_ctx": n, "cumulative_broker_ctx": served,
                            "cumulative_relay_ctx": relay,
                            "cumulative_profit_wei": profit})
    if n != len(workload) or served != stats["served"] or relay != stats["relayed"]:
        raise ValueError(f"{arm_dir.name}: reconstructed CTX counts mismatch")
    if profit != total_fee or profit != stats["b2e"]["fee_broker_wei_served"]:
        raise ValueError(f"{arm_dir.name}: broker fee mismatch")
    if a + b != 2 * initial_wei + profit:
        raise ValueError(f"{arm_dir.name}: broker balance and fee conservation failed")
    return balance_rows, profit_rows, {"first_relay_block": first_relay,
                                       "source_final_eth": a / ETH,
                                       "target_final_eth": b / ETH,
                                       "broker_fee_eth": profit / ETH,
                                       "broker_ctx": served, "relay_ctx": relay,
                                       "tdr_transfers": len(moves)}


def plot_figures(balance_rows: list[dict], profit_rows: list[dict], out_dir: Path,
                 src: int, dst: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = out_dir / "figs"
    figs.mkdir(exist_ok=True)
    off = [r for r in balance_rows if r["arm"] == "plain"]
    fig, ax = plt.subplots(figsize=(8, 5), dpi=160)
    x = [int(r["block"]) - int(off[0]["block"]) for r in off]
    ax.plot(x, [int(r["source_balance_wei"]) / ETH for r in off],
            linestyle="--", color="#229fd9", label=f"Shard A (S{src})")
    ax.plot(x, [int(r["target_balance_wei"]) / ETH for r in off],
            color="#229fd9", label=f"Shard B (S{dst})")
    ax.set(xlabel="Block heights since first observation",
           ylabel="Broker sub-account balances (ETH)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(figs / "balances.png")
    fig.savefig(figs / "balances.pdf")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 6), dpi=160)
    for arm, label, color in (("tdr", "With TDR", "#d92d00"),
                              ("plain", "Without TDR", "#229fd9")):
        subset = [r for r in profit_rows if r["arm"] == arm]
        ax.plot([int(r["confirmed_ctx"]) for r in subset],
                [int(r["cumulative_profit_wei"]) / ETH for r in subset],
                label=label, color=color, linewidth=2.5)
    ax.set(xlabel="# of confirmed user cross-shard transfers",
           ylabel="Broker retained fees (ETH)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(figs / "profit.png")
    fig.savefig(figs / "profit.pdf")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--reuse-plain", type=Path,
                        help="reuse a verified Plain arm from a prior exp022 output")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.smoke and args.reuse_plain:
        parser.error("--smoke and --reuse-plain cannot be combined")
    overrides = list(args.overrides)
    if args.smoke:
        overrides = ["exp.count=60", "exp.rate=2", "exp.balances_eth=0.2",
                     "broker.initial_balance_eth=0.2", "exp.tdr_window_blocks=5",
                     "exp.tdr_chi_blocks=2", *overrides]
    cfg = apply_overrides(load_config(args.config), overrides)
    workload = make_workload(cfg)
    total_wei = sum(r["amount_wei"] for r in workload)
    fee_total, fee_broker, _ = b2e_fees(cfg)
    print(f"exp022: {len(workload):,} A->B CTXs; {total_wei / ETH:g} ETH principal; "
          f"{fee_broker / ETH:g} ETH broker fee per served CTX; two paired arms")
    if args.dry_run:
        print("Dry run: no chain started or output written")
        return 0
    previous = None
    if args.reuse_plain:
        old_dir = args.reuse_plain.resolve()
        previous = json.loads((old_dir / "summary.json").read_text(encoding="utf-8"))
        if previous.get("experiment") != "exp022_directional_profit" or "plain" not in previous.get("arms", {}):
            raise ValueError("--reuse-plain needs an exp022 output with a completed Plain arm")
        old_params = deepcopy(previous["params"])
        new_params = deepcopy(to_params_dict(cfg))
        old_params["exp"].pop("drain_tail_s", None)
        new_params["exp"].pop("drain_tail_s", None)
        if old_params != new_params:
            raise ValueError("Plain reuse permits changing only exp.drain_tail_s")
    out_dir = (args.output or HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    fields = list(workload[0])
    workload_path = out_dir / "workload.csv"
    _write_csv(workload_path, workload, fields)
    workload_sha = hashlib.sha256(workload_path.read_bytes()).hexdigest()
    if previous and previous["workload_sha256"] != workload_sha:
        raise ValueError("Plain reuse workload SHA256 mismatch")
    report = {"experiment": "exp022_directional_profit", "status": "running",
              "mode": "smoke" if args.smoke else "full", "params": to_params_dict(cfg),
              "workload_sha256": workload_sha, "workload_count": len(workload),
              "fee_total_wei_per_ctx": fee_total,
              "fee_broker_wei_per_served_ctx": fee_broker, "arms": {}}
    watchdog = RSSWatchdog(float(cfg.exp["rss_cap_mb"]))
    watchdog.start()
    all_balance, all_profit = [], []
    try:
        initial = _wei(cfg.exp["balances_eth"])
        src, dst = int(cfg.exp["source_shard"]), int(cfg.exp["target_shard"])
        for arm, engine in (("plain", "plain"), ("tdr", "proportional")):
            if arm == "plain" and previous:
                arm_dir = old_dir / "plain"
                stats = previous["arms"]["plain"]["stats"]
                print(f"Reusing verified Plain arm: {arm_dir}")
            else:
                arm_dir = out_dir / arm
                print(f"Running {arm}...")
                stats = run_arm(cfg, arm, workload, total_wei, out_dir, watchdog,
                                engine=engine, fund_eth=float(cfg.exp["balances_eth"]))
            balances, profits, metrics = analyze_arm(
                arm_dir, workload, stats, initial, src, dst)
            all_balance.extend(balances)
            all_profit.extend(profits)
            report["arms"][arm] = {"source": str(arm_dir),
                                    "stats": stats, "metrics": metrics}
        _write_csv(out_dir / "balance_by_block.csv", all_balance, list(all_balance[0]))
        _write_csv(out_dir / "profit_by_tx.csv", all_profit, list(all_profit[0]))
        plot_figures(all_balance, all_profit, out_dir, src, dst)
        report["status"] = "complete"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        watchdog.stop()
        (out_dir / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
