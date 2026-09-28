#!/usr/bin/env python3
"""Exp012: trace placement concentration, fixed capacity, Plain/Valve/EWMA.

--check / --dry-run: validate and print plan, no outputs or nodes.
--prepare-only: scan raw trace and audit all selected layouts, no nodes.
Default: prepare, execute, aggregate. Exit status concerns integrity, not wins.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from brokerlab.chain import resolve_anvil_bin
from brokerlab.config import apply_overrides, load_config, to_params_dict
from brokerlab.mapped_workload import (candidate_pool, prepare_scenario, sha256_file,
                                       theoretical, write_json)

EXP003 = HERE.parent / "exp003_tdr_on_off" / "run.py"


def resolve(cfg, selection=None):
    if cfg.chain.num_shards != 16:
        raise ValueError("exp012 requires exactly 16 shards")
    for key in ("sessions", "candidate_count", "ctx_per_broker", "rate", "tdr_window_blocks"):
        v = cfg.exp[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 or int(v) != v:
            raise ValueError(f"exp.{key} must be a positive integer")
    balance, valve = float(cfg.exp["balances_eth"]), float(cfg.exp["valve_threshold"])
    eps, hl = float(cfg.exp["ewma_epsilon"]), float(cfg.exp["ewma_half_life"])
    if not all(math.isfinite(v) for v in (balance, valve, eps, hl)) or balance <= 0 or valve <= 1 or not 0 <= eps < 1 or hl <= 0:
        raise ValueError("Invalid balance / Valve threshold / EWMA parameters")
    if cfg.chain.gas_limit < 21000 or cfg.chain.block_time_s <= 0:
        raise ValueError("Positive block time and capacity >= one transfer required")
    if cfg.b2e.enabled or not cfg.exp.get("collect_block_capacity"):
        raise ValueError("exp012 requires B2E off and block-capacity collection on")
    scenarios = cfg.exp["scenarios"]
    names = [s["name"] for s in scenarios]
    if not names or len(names) != len(set(names)) or any(not re.fullmatch(r"[a-z0-9_]+", n) for n in names):
        raise ValueError("Scenario names must be unique safe directory names")
    for s in scenarios:
        theoretical(s)  # Validates bit count and hotspot assignments.
    if selection:
        wanted = selection.split(",")
        if len(wanted) != len(set(wanted)) or set(wanted) - set(names):
            raise ValueError(f"Unknown/duplicate scenarios: {selection}")
        scenarios = [s for s in scenarios if s["name"] in wanted]
    arms = f"plain@{balance},valve@{balance}@{valve},topup@{balance}@@{eps}@@{hl}"
    cfg = replace(cfg, exp={**cfg.exp, "arms": arms, "scenarios": scenarios},
                  broker=replace(cfg.broker, initial_balance_eth=balance))
    trace = Path(cfg.traffic.real_csv_path)
    if not trace.is_absolute():
        trace = ROOT / trace
        cfg = replace(cfg, traffic=replace(cfg.traffic, real_csv_path=str(trace)))
    if not trace.is_file():
        raise FileNotFoundError(trace)
    count = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    if cfg.exp["candidate_count"] < count:
        raise ValueError("candidate_count must be >= CTX count; allow extra for intra-shard filtering")
    plan = {"experiment": cfg.label, "trace": str(trace), "anvil": resolve_anvil_bin(cfg.chain.anvil_bin),
            "ctx_per_arm": count, "sessions": cfg.exp["sessions"],
            "arm_runs": len(scenarios) * cfg.exp["sessions"] * 3,
            "total_ctx": len(scenarios) * cfg.exp["sessions"] * 3 * count,
            "balance_per_broker_per_shard_eth": balance, "total_broker_liquidity_eth": balance * 16 * cfg.scale.num_brokers,
            "valve_threshold": valve, "valve_trigger_eth": valve * balance,
            "arms": arms, "gas_limit": cfg.chain.gas_limit,
            "simple_transfers_per_shard_block": cfg.chain.gas_limit // 21000,
            "ports": [cfg.chain.base_port, cfg.chain.base_port + 15],
            "layouts": [{**s, **theoretical(s)} for s in scenarios]}
    return cfg, plan


def aggregate(records, output, expected_count):
    metrics, invalid = [], []
    for rec in records:
        path = Path(rec["summary"]) if rec.get("summary") else None
        d = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
        valid = (rec["returncode"] == 0 and d.get("passed") is True
                 and len(d.get("arms", [])) == 3
                 and all(a.get("n") == expected_count and a.get("block_capacity") for a in d.get("arms", [])))
        rec["integrity_valid"] = bool(valid)
        if not valid:
            invalid.append({"scenario": rec["scenario"], "session": rec["session"], "summary": rec.get("summary")})
        for a in d.get("arms", []):
            b = a.get("block_capacity", {})
            metrics.append({"scenario": rec["scenario"], "session": rec["session"], "route_seed": rec["route_seed"],
                            "arm": a["arm"], "valid": bool(valid), "n": a["n"],
                            "relay": a["relayed"], "relay_fraction": a["relayed"] / a["n"] if a["n"] else None,
                            "tdr_transfers": a["tdr"]["transfers_done"], "tdr_events": a["tdr"]["events_opened"],
                            "tdr_legs": a["legs"]["tdr_legs"],
                            "total_chain_legs": a["legs"]["ctx_legs"] + a["legs"]["tdr_legs"],
                            "throughput_ctx_per_s": a["throughput_ctx_per_s"],
                            "achieved_injection_ctx_per_s": a["coord"].get("achieved_injection_ctx_per_s"),
                            "backlog_peak": a["coord"].get("backlog_peak"),
                            "e2e_p95_s": a["e2e_secs"]["p95"],
                            "peak_mean_gas_utilization": b.get("max_mean_utilization"),
                            "peak_full_block_fraction": b.get("max_full_block_fraction")})
    comparisons = []
    for rec in records:
        rows = [r for r in metrics if r["scenario"] == rec["scenario"] and r["session"] == rec["session"] and r["valid"]]
        ewma = next((r for r in rows if r["arm"].startswith("topup@")), None)
        if not ewma:
            continue
        for base in rows:
            if base is ewma:
                continue
            comparisons.append({"scenario": rec["scenario"], "session": rec["session"], "baseline": base["arm"],
                                "relay_fraction_reduction": base["relay_fraction"] - ewma["relay_fraction"],
                                "throughput_change": ewma["throughput_ctx_per_s"] - base["throughput_ctx_per_s"],
                                "extra_chain_legs": ewma["total_chain_legs"] - base["total_chain_legs"]})
    aggregates = []
    for scenario, arm in sorted({(r["scenario"], r["arm"]) for r in metrics}):
        rows = [r for r in metrics if r["scenario"] == scenario and r["arm"] == arm and r["valid"]]
        stats = {}
        for key in ("relay_fraction", "throughput_ctx_per_s", "tdr_legs", "e2e_p95_s", "peak_full_block_fraction"):
            values = [r[key] for r in rows if r[key] is not None]
            stats[key] = {"n": len(values), "median": statistics.median(values), "min": min(values), "max": max(values)} if values else None
        aggregates.append({"scenario": scenario, "arm": arm, "stats": stats})
    with open(output / "metrics.csv", "w", newline="", encoding="utf-8-sig") as f:
        if metrics:
            writer = csv.DictWriter(f, fieldnames=list(metrics[0]))
            writer.writeheader()
            writer.writerows(metrics)
    report = {"all_integrity_valid": bool(records) and not invalid, "invalid_runs": invalid,
              "runs": records, "aggregates": aggregates, "paired_ewma_comparisons": comparisons,
              "interpretation": "Concentration is not guaranteed congestion or net flow. Route-seed repeats use one trace window. Wins are research outcomes, not integrity gates."}
    write_json(output / "report.json", report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--scenarios", help="Comma-separated names; default all configured layouts")
    ap.add_argument("--check", "--dry-run", dest="check", action="store_true")
    ap.add_argument("--prepare-only", action="store_true")
    args = ap.parse_args()
    cfg, plan = resolve(apply_overrides(load_config(args.config), args.overrides), args.scenarios)
    console_plan = {**plan, "layouts": [
        {"name": s["name"], "bits": s["bits"], "hotspots": s["hotspots"],
         "theoretical_hot_address_share": s["hot_address_share"],
         "theoretical_peak_ctx_endpoint_share": max(s["iid_ctx_endpoint_shares"])}
        for s in plan["layouts"]]}
    print(json.dumps(console_plan, ensure_ascii=False, indent=2), flush=True)
    if args.check:
        return 0
    output = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True)
    print(f"OUTPUT: {output}", flush=True)
    write_json(output / "plan.json", plan)
    (output / "resolved.yaml").write_text(yaml.safe_dump(to_params_dict(cfg), sort_keys=False), encoding="utf-8")
    sources = [Path(__file__), HERE / "config.yaml", EXP003, ROOT / "brokerlab/mapped_workload.py",
               ROOT / "brokerlab/tdr_hook.py", ROOT / "brokerlab/tdr_policy.py", ROOT / "brokerlab/broker_engine.py"]
    write_json(output / "source_hashes.json", {str(p): sha256_file(p) for p in sources})
    print("Reading common pre-mapping trace prefix...", flush=True)
    pool, filters = candidate_pool(cfg, int(cfg.exp["candidate_count"]))
    trace = Path(cfg.traffic.real_csv_path)
    write_json(output / "source_manifest.json", {"trace": str(trace), "size": trace.stat().st_size,
               "mtime_ns": trace.stat().st_mtime_ns, "filters": filters,
               "candidate_sha256": hashlib.sha256(json.dumps(pool, sort_keys=True).encode()).hexdigest()})
    prepared, audit_rows = {}, []
    for scenario in cfg.exp["scenarios"]:
        path, audit = prepare_scenario(pool, scenario, cfg, plan["ctx_per_arm"], output / scenario["name"])
        prepared[scenario["name"]] = (path, audit)
        s = audit["selected_ctx"]
        audit_rows.append({"scenario": scenario["name"], "selected_ctx": s["n"],
                           "intra_fraction": audit["selection"]["intra_fraction"],
                           "endpoint_hhi": s["endpoint_hhi"], "peak_endpoint_share": s["peak_endpoint_share"],
                           "net_imbalance_ratio": s["net_imbalance_ratio"],
                           "max_target_capacity_ratio": s["max_target_capacity_ratio"]})
        print(json.dumps(audit_rows[-1]), flush=True)
    with open(output / "mapping_audit.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(audit_rows[0]))
        writer.writeheader()
        writer.writerows(audit_rows)
    if args.prepare_only:
        print(f"PREPARED: {output}; no nodes started.", flush=True)
        return 0
    records = []
    for session in range(int(cfg.exp["sessions"])):
        # Rotate method order by session to reduce always-first/always-last effects.
        tokens = cfg.exp["arms"].split(",")
        shift = session % len(tokens)
        arms = ",".join(tokens[shift:] + tokens[:shift])
        scenarios = cfg.exp["scenarios"]
        shift = session % len(scenarios)
        for scenario in scenarios[shift:] + scenarios[:shift]:
            name = scenario["name"]
            path, audit = prepared[name]
            route_seed = int(cfg.exp["route_seed"]) + 10 * session
            child_cfg = replace(cfg, exp={**cfg.exp, "arms": arms, "route_seed": route_seed,
                                "prepared_workload_path": str(path.resolve()),
                                "prepared_workload_sha256": audit["workload_sha256"]})
            folder = output / name / f"session_{session + 1}"
            folder.mkdir()
            child_path = folder / "config.yaml"
            child_path.write_text(yaml.safe_dump(to_params_dict(child_cfg), sort_keys=False), encoding="utf-8")
            command = [sys.executable, "-u", str(EXP003), "--config", str(child_path), "--out-root", str(folder)]
            print(f"RUN {name} session={session + 1} seed={route_seed}", flush=True)
            # Tee child logs to console and disk; Windows spawn runs the real exp003 path.
            with open(folder / "run.log", "w", encoding="utf-8") as log:
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace",
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"}) as proc:
                    for line in proc.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                    rc = proc.wait()
            summaries = sorted(folder.glob("*/summary.json"))
            records.append({"scenario": name, "session": session + 1, "route_seed": route_seed,
                            "returncode": rc, "summary": str(summaries[-1]) if summaries else None})
            write_json(output / "run_status.json", records)
            if rc:
                aggregate(records, output, plan["ctx_per_arm"])
                print("Stopped on execution/integrity failure; see run.log and report.json.")
                return 1
    report = aggregate(records, output, plan["ctx_per_arm"])
    print(f"REPORT: {output / 'report.json'}", flush=True)
    return 0 if report["all_integrity_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
