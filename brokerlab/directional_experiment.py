"""Shared CLI for directional experiments. Preparation never starts Anvil.

Report integrity is separate from experimental outcomes. Incomplete runs cannot
be labeled complete, and combined metrics are calculated per run before median.
"""
import argparse
import csv
import json
import hashlib
import math
import os
import re
import socket
import statistics
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml

from .config import load_config, apply_overrides, to_params_dict
from .mapped_workload import candidate_pool, sha256_file, write_json, load_prepared
from .directional_workload import (snapshot_source, natural_windows, learned_layouts,
                                   sampled_layouts, export_scenario, ensure_order)
from .controlled_direction import controlled_layouts, export_controlled, validate_settings
from .two_shard_factorial import (two_shard_layouts, export_two_shard,
                                  validate_settings as validate_two_shard_settings)

ROOT = Path(__file__).resolve().parents[1]
EXP003 = ROOT / "experiments/exp003_tdr_on_off/run.py"
BUILDERS = {"natural": natural_windows, "learned": learned_layouts, "sampled": sampled_layouts,
            "controlled": controlled_layouts, "two_shard": two_shard_layouts}


def resolve(cfg, method):
    if method not in BUILDERS or cfg.chain.num_shards != 16:
        raise ValueError("Unknown method or num_shards != 16")
    for key in ("sessions", "candidate_count", "ctx_per_broker", "rate", "audit_window_blocks"):
        v = cfg.exp[key]
        if type(v) is not int or v <= 0:
            raise ValueError(f"exp.{key} must be a positive integer")
    count = cfg.scale.num_brokers * cfg.exp["ctx_per_broker"]
    if cfg.scale.num_brokers <= 0 or cfg.exp["candidate_count"] < count:
        raise ValueError("Invalid broker count / insufficient candidate_count")
    if type(cfg.exp["target_shard"]) is not int or not 0 <= cfg.exp["target_shard"] < 16:
        raise ValueError("target_shard must be 0..15")
    b, v, e, h, hard = [float(cfg.exp[k]) for k in
                       ("balances_eth", "valve_threshold", "ewma_epsilon", "ewma_half_life", "hard_epsilon")]
    prop = float(cfg.exp["tdr_epsilon"])
    if not all(math.isfinite(x) for x in (b,v,e,h,hard)) or b <= 0 or v <= 1 or not 0 <= e < 1 or h <= 0 or not 0 <= hard < 1:
        raise ValueError("Invalid balance/Valve/epsilon/half-life")
    if not 0 < cfg.traffic.value_floor_eth <= cfg.traffic.value_cap_eth or cfg.b2e.enabled:
        raise ValueError("Positive amount interval and B2E off required")
    if not math.isfinite(prop) or not 0 <= prop < 1:
        raise ValueError("Invalid proportional epsilon")
    if method == "learned":
        if not 0 < cfg.exp["train_candidates"] < cfg.exp["candidate_count"]:
            raise ValueError("train_candidates must leave a held-out pool")
        if not 0 < cfg.exp["max_receiver_address_fraction"] <= 1 or not 0 < cfg.exp["receiver_score_min"] <= 1 or cfg.exp["min_address_observations"] < 1:
            raise ValueError("Invalid receiving address selection settings")
    if method == "sampled":
        ratios = cfg.exp["direction_ratios"]
        if not ratios or any(not 0 < p < 1 for p in ratios) or len({round(p*100) for p in ratios}) != len(ratios):
            raise ValueError("Distinct direction_ratios in (0,1) required")
        if not 0 <= cfg.exp["background_fraction"] < 1:
            raise ValueError("background_fraction must be in [0,1)")
    if method == "controlled":
        validate_settings(cfg.exp["control_probabilities"], cfg.exp["control_seed"])
    if method == "two_shard":
        validate_two_shard_settings(cfg.exp["hotspot_fractions"], cfg.exp["direction_ratios"],
            cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"],
            cfg.exp["direction_seed"], cfg.exp["hot_selector_bits_per_address"])
    arms = f"plain@{b},valve@{b}@{v},tdr@{b}@@{prop},topup@{b}@@{hard},topup@{b}@@{e}@@{h}"
    cfg = replace(cfg, exp={**cfg.exp, "arms": arms}, broker=replace(cfg.broker, initial_balance_eth=b))
    return cfg, count


def tags(cfg):
    out = []
    for token in cfg.exp["arms"].split(","):
        parts = token.split("@")
        out.append(parts[0] + "".join("@" + str(float(x)) for x in parts[1:] if x))
    return out


def csv_rows(path, rows):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def summarize(records, output, cfg, scenarios):
    expected = {(s["name"], i) for s in scenarios for i in range(1,cfg.exp["sessions"]+1)}
    metrics, valid_pairs = [], set()
    for rec in records:
        p = Path(rec["summary"]) if rec.get("summary") else None
        d = json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}
        arms = d.get("arms", [])
        valid = (rec["returncode"] == 0 and d.get("passed") is True and d.get("status", {}).get("complete") is True
                 and len(arms) == len(tags(cfg)) and {a["arm"] for a in arms} == set(tags(cfg))
                 and all(a["n"] == cfg.scale.num_brokers*cfg.exp["ctx_per_broker"]
                         and not a["failed"] and not a["tdr"]["transfers_lost"]
                         and a.get("gates") and all(a["gates"].values()) and a["burn_reconcile"]["ok"] for a in arms))
        rec["integrity_valid"] = bool(valid)
        if valid:
            valid_pairs.add((rec["scenario"], rec["session"]))
        for a in arms:
            metrics.append(dict(scenario=rec["scenario"], session=rec["session"], arm=a["arm"], valid=bool(valid),
                                relay=a["relayed"], tdr_events=a["tdr"]["events_opened"],
                                tdr_transfers=a["tdr"]["transfers_done"],
                                relay_plus_transfers=a["relayed"]+a["tdr"]["transfers_done"],
                                tdr_chain_legs=a["legs"]["tdr_legs"],
                                throughput=a["throughput_ctx_per_s"],
                                injection=a["coord"].get("achieved_injection_ctx_per_s")))
    aggregates = []
    keys = ("relay", "tdr_events", "tdr_transfers", "relay_plus_transfers", "tdr_chain_legs", "throughput", "injection")
    for s in scenarios:
        for tag in tags(cfg):
            rows = [r for r in metrics if r["scenario"] == s["name"] and r["arm"] == tag and r["valid"]]
            if rows:
                aggregates.append(dict(scenario=s["name"], arm=tag, sessions=len(rows),
                    **{k: statistics.median(r[k] for r in rows if r[k] is not None)
                       if any(r[k] is not None for r in rows) else None for k in keys}))
    complete = (valid_pairs == expected and len(records) == len(expected)
                and all(r["integrity_valid"] for r in records))
    report = dict(complete=complete, expected_runs=len(expected), valid_runs=len(valid_pairs),
                  missing_runs=sorted(expected-valid_pairs), runs=records, aggregates=aggregates,
                  caveat="Medians of per-session sums; not sum of medians. TDR transfers are not chain legs. No success gate requires EWMA to win.")
    csv_rows(output / "metrics.csv", metrics)
    csv_rows(output / "comparison.csv", aggregates)
    write_json(output / "report.json", report)
    md = ["# 实验汇总", "", f"完整完成：{complete}；有效场次：{len(valid_pairs)}/{len(expected)}。",
          "", "以下为有效session的中位数；部分完成时不是最终结果。TDR额外CTX=成功资金转移数，不是链上段数。", ""]
    for scenario in scenarios:
        md.extend([f"## {scenario['name']}", "", "| 方案 | 样本数 | Relay数量 | TDR触发次数 | TDR额外CTX | Relay+TDR额外CTX |",
                   "|---|---:|---:|---:|---:|---:|"])
        for a in aggregates:
            if a["scenario"] == scenario["name"]:
                md.append(f"| {a['arm']} | {a['sessions']} | {a['relay']:g} | {a['tdr_events']:g} | {a['tdr_transfers']:g} | {a['relay_plus_transfers']:g} |")
        md.append("")
    (output / "report.md").write_text("\n".join(md), encoding="utf-8")
    return report


def ports_free(cfg):
    for port in range(cfg.chain.base_port, cfg.chain.base_port+cfg.chain.num_shards):
        with socket.socket() as s:
            s.settimeout(0.1)
            if s.connect_ex(("127.0.0.1",port)) == 0:
                raise RuntimeError(f"Port {port} occupied; stop your other experiment or change base_port. No processes killed.")


def fingerprint(cfg, method):
    # Frozen datasets depend on selection and account/layout settings, not routing seeds.
    return dict(method=method, traffic=to_params_dict(cfg)["traffic"], scale=to_params_dict(cfg)["scale"],
                gas_limit=cfg.chain.gas_limit, num_shards=cfg.chain.num_shards,
                count=cfg.scale.num_brokers*cfg.exp["ctx_per_broker"],
                selection={k: cfg.exp[k] for k in ("candidate_count", "target_shard", "audit_window_blocks", "rate",
                    "balances_eth", "train_candidates", "max_receiver_address_fraction", "receiver_score_min",
                    "min_address_observations", "direction_ratios", "background_fraction",
                    "control_probabilities", "control_seed", "hotspot_fractions",
                    "hotspot_shard_a", "hotspot_shard_b", "direction_seed",
                    "hot_selector_bits_per_address") if k in cfg.exp})


def prepare(cfg, method, directory):
    print("Creating/verifying immutable source CSV snapshot...", flush=True)
    source = Path(cfg.traffic.real_csv_path)
    snap_root = Path(cfg.exp["snapshot_dir"])
    if not snap_root.is_absolute():
        snap_root = ROOT / snap_root
    snapshot, source_manifest = snapshot_source(source, snap_root)
    read_cfg = replace(cfg, traffic=replace(cfg.traffic, real_csv_path=str(snapshot)))
    print("Filtering chronological candidate pool from snapshot...", flush=True)
    pool, filters = candidate_pool(read_cfg, cfg.exp["candidate_count"])
    ensure_order(pool)
    layouts, design = BUILDERS[method](pool, cfg, cfg.scale.num_brokers*cfg.exp["ctx_per_broker"])
    source_manifest = {**source_manifest, "filters": filters,
                       "candidate_hash": hashlib.sha256(json.dumps(pool, sort_keys=True).encode()).hexdigest()}
    write_json(directory / "design.json", design)
    prepared, audit_rows = [], []
    exporter = (export_controlled if method == "controlled" else
                export_two_shard if method == "two_shard" else export_scenario)
    for name, rows, mapping, info in layouts:
        item = exporter(directory / name, name, rows, mapping, info, cfg, source_manifest)
        check_cfg = replace(cfg, exp={**cfg.exp, "prepared_workload_path": item["path"],
                                    "prepared_workload_sha256": item["sha256"]})
        load_prepared(check_cfg)
        a = item["audit"]
        if method == "two_shard":
            audit_rows.append(dict(scenario=name, n=len(rows),
                source_first_block=a["source_first_block"], source_last_block=a["source_last_block"],
                **{k: v for k, v in a["hotspot_control"].items() if k != "per_window"}))
        else:
            audit_rows.append(dict(scenario=name, n=len(rows), net_fraction=a["target_net_fraction"],
                                   direction_ratio=a["target_direction_ratio"], persistence=a["positive_window_fraction"],
                                   incoming=a["target_in_count"], outgoing=a["target_out_count"],
                                   source_first_block=a["source_first_block"], source_last_block=a["source_last_block"]))
            if method == "controlled":
                audit_rows[-1].update(a["control"])
        prepared.append({k:v for k,v in item.items() if k != "audit"})
        print(json.dumps(audit_rows[-1]), flush=True)
    csv_rows(directory / "direction_audit.csv", audit_rows)
    bundle = dict(schema=1, fingerprint=fingerprint(cfg,method), source=source_manifest,
                  scenarios=prepared, caveat="Direction strength is measured, not assumed; inspect audit before interpreting algorithm outcomes")
    write_json(directory / "prepared.json", bundle)
    return bundle


def main(here, method, argv=None):
    here = Path(here).resolve()
    ap = argparse.ArgumentParser(description=f"Directional experiment: {method}")
    ap.add_argument("--config", type=Path, default=here / "config.yaml")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--dry-run", "--check", dest="dry_run", action="store_true")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--prepared", type=Path, help="Reuse a prepared.json or its directory; validate hashes and selection config")
    args = ap.parse_args(argv)
    cfg, count = resolve(apply_overrides(load_config(args.config), args.set), method)
    scenario_count = (len(cfg.exp["control_probabilities"]) if method == "controlled" else
                      2 + len(cfg.exp["hotspot_fractions"])*len(cfg.exp["direction_ratios"])
                      if method == "two_shard" else
                      1+len(cfg.exp["direction_ratios"]) if method == "sampled" else 3)
    plan = dict(method=method, ctx_per_arm=count, sessions=cfg.exp["sessions"], scenarios=scenario_count,
                arms=cfg.exp["arms"], total_ctx=count*5*scenario_count*cfg.exp["sessions"],
                ports=[cfg.chain.base_port,cfg.chain.base_port+15], gas_limit=cfg.chain.gas_limit,
                params=to_params_dict(cfg))
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    output = here / "out" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True)
    write_json(output / "plan.json", plan)
    (output / "resolved.yaml").write_text(yaml.safe_dump(to_params_dict(cfg), sort_keys=False), encoding="utf-8")
    sources = [Path(__file__), ROOT / "brokerlab/directional_workload.py",
               ROOT / "brokerlab/controlled_direction.py", ROOT / "brokerlab/mapped_workload.py",
               EXP003, args.config]
    if method == "two_shard":
        sources.append(ROOT / "brokerlab/two_shard_factorial.py")
    write_json(output / "source_hashes.json", {str(p): sha256_file(p) for p in sources})
    records, bundle = [], None
    try:
        if args.prepared:
            p = args.prepared / "prepared.json" if args.prepared.is_dir() else args.prepared
            bundle = json.loads(p.read_text(encoding="utf-8"))
            if bundle["fingerprint"] != fingerprint(cfg,method):
                raise ValueError("Prepared selection/config differs; prepare fresh data for changed settings")
            names = [item["name"] for item in bundle["scenarios"]]
            if len(names) != scenario_count or len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z0-9_]+", name) for name in names):
                raise ValueError("Prepared scenario names/count invalid")
            for item in bundle["scenarios"]:
                load_prepared(replace(cfg,exp={**cfg.exp,"prepared_workload_path":item["path"],"prepared_workload_sha256":item["sha256"]}))
            write_json(output / "prepared.json", bundle)
        else:
            bundle = prepare(cfg,method,output)
        if args.prepare_only:
            write_json(output / "status.json", dict(state="prepared", nodes_started=False))
            print(f"PREPARED (no chains): {output}", flush=True)
            return 0
        scenarios = bundle["scenarios"]
        for session in range(cfg.exp["sessions"]):
            tokens = cfg.exp["arms"].split(",")
            shift = session % len(tokens)
            arms = ",".join(tokens[shift:]+tokens[:shift])
            shift = session % len(scenarios)
            for item in scenarios[shift:]+scenarios[:shift]:
                ports_free(cfg)
                folder = output / item["name"] / f"session_{session+1}"
                folder.mkdir(parents=True)
                child = replace(cfg,exp={**cfg.exp,"arms":arms,"route_seed":cfg.exp["route_seed"]+10*session,
                    "prepared_workload_path":item["path"],"prepared_workload_sha256":item["sha256"]})
                cp = folder / "config.yaml"
                cp.write_text(yaml.safe_dump(to_params_dict(child),sort_keys=False),encoding="utf-8")
                command=[sys.executable,"-u",str(EXP003),"--config",str(cp),"--out-root",str(folder)]
                print(f"RUN {item['name']} session={session+1}",flush=True)
                write_json(output / "status.json",dict(state="running",scenario=item["name"],session=session+1))
                with (folder / "run.log").open("w",encoding="utf-8") as log:
                    with subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                        text=True,encoding="utf-8",errors="replace",env={**os.environ,"PYTHONIOENCODING":"utf-8","PYTHONUTF8":"1"}) as proc:
                        for line in proc.stdout:
                            print(line,end="",flush=True)
                            log.write(line)
                        rc=proc.wait()
                found=list(folder.glob("*/summary.json"))
                records.append(dict(scenario=item["name"],session=session+1,returncode=rc,
                                     summary=str(found[0]) if len(found)==1 else None))
                report=summarize(records,output,cfg,scenarios)
                write_json(output / "run_status.json",records)
                if rc or not records[-1]["integrity_valid"]:
                    raise RuntimeError("Execution/integrity failure; stopped. Inspect report.json and run.log")
        write_json(output / "status.json",dict(state="complete",report=str(output/"report.json")))
        print(f"REPORT: {output / 'report.json'}",flush=True)
        return 0 if report["complete"] else 1
    except BaseException as exc:
        write_json(output / "status.json",dict(state="interrupted" if isinstance(exc,KeyboardInterrupt) else "failed",error=str(exc)))
        raise
