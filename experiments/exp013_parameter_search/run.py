#!/usr/bin/env python3
"""Resumable staged search based on exp012's workloads and exp003 execution."""
import argparse
import csv
import json
import math
import os
import random
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
sys.path.insert(0, str(HERE))

import psutil
import yaml
from brokerlab.chain import resolve_anvil_bin
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.mapped_workload import candidate_pool, prepare_scenario, sha256_file, theoretical
from search import (METHODS, arm, candidates, coverage_probes, identity, neighbors,
                    pareto_layers, shortlist, trial_config)

EXP003 = HERE.parent / "exp003_tdr_on_off/run.py"
STAGES = ("coarse", "refine", "validate")


def save(path, value):
    """Atomic checkpoint; interruption never leaves a half-written JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sources():
    paths = [Path(__file__), HERE / "search.py", EXP003, *sorted((ROOT / "brokerlab").glob("*.py"))]
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in paths}


def trace_stamp(cfg):
    p = Path(cfg.traffic.real_csv_path)
    stat = p.stat()
    return {"path": str(p.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def resolve(cfg, scenarios=None, balances=None):
    e = dict(cfg.exp)
    for key in ("candidate_count", "ctx_per_broker", "shortlist_per_method", "validation_windows", "rate"):
        if type(e[key]) is not int or e[key] <= 0:
            raise ValueError(f"exp.{key} must be a positive integer")
    if type(e["warmup_arrival_blocks"]) is not int or e["warmup_arrival_blocks"] < 0:
        raise ValueError("warmup_arrival_blocks must be a nonnegative integer")
    bounds = {"balances": lambda v: v > 0, "valve_thresholds": lambda v: v > 1,
              "epsilons": lambda v: 0 <= v < 1, "windows": lambda v: v >= 1 and int(v) == v,
              "half_lives": lambda v: v >= 1 and int(v) == v}
    for key, predicate in bounds.items():
        values = e[key]
        if (not isinstance(values, list) or not values or values != sorted(set(values))
                or any(type(v) not in (int, float) or not math.isfinite(v) or not predicate(v) for v in values)):
            raise ValueError(f"Invalid sorted unique grid exp.{key}: {values}")
    for full in ("epsilons", "windows", "half_lives"):
        subset = e["coarse_" + full]
        if not isinstance(subset, list) or not subset or len(set(subset)) != len(subset) or not set(subset) <= set(e[full]):
            raise ValueError(f"coarse_{full} must be a nonempty unique subset of {full}")
    for key in ("search_seed", "train_seed"):
        if type(e[key]) is not int:
            raise ValueError(f"exp.{key} must be an integer")
    seeds = e["validation_seeds"]
    if not isinstance(seeds, list) or not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int for s in seeds):
        raise ValueError("validation_seeds must be nonempty unique integers")
    names = [s["name"] for s in e["scenarios"]]
    if not names or len(names) != len(set(names)) or any(not re.fullmatch(r"[a-z0-9_]+", s) for s in names):
        raise ValueError("Invalid scenario names")
    for s in e["scenarios"]:
        theoretical(s)
    if scenarios:
        chosen = scenarios.split(",")
        if len(set(chosen)) != len(chosen) or not set(chosen) <= set(names):
            raise ValueError("Unknown or duplicate --scenarios")
        e["scenarios"] = [s for s in e["scenarios"] if s["name"] in chosen]
    if balances:
        chosen = [float(b) for b in balances.split(",")]
        if len(set(chosen)) != len(chosen) or not set(chosen) <= set(e["balances"]):
            raise ValueError("--balances must be a unique subset of the configured grid")
        e["balances"] = [b for b in e["balances"] if b in chosen]
    if cfg.chain.num_shards != 16 or cfg.chain.gas_limit < 21000 or cfg.chain.block_time_s <= 0:
        raise ValueError("Require 16 shards, positive block time and capacity >= one transfer")
    if cfg.b2e.enabled or not e.get("collect_block_capacity"):
        raise ValueError("Require B2E off and block-capacity collection on")
    if cfg.scale.num_brokers < 1:
        raise ValueError("Need at least one broker")
    n = cfg.scale.num_brokers * e["ctx_per_broker"]
    if e["candidate_count"] < n:
        raise ValueError("candidate_count must cover CTX count plus intra-shard filtering")
    trace = Path(cfg.traffic.real_csv_path)
    if not trace.is_absolute():
        trace = ROOT / trace
    if not trace.is_file():
        raise FileNotFoundError(trace)
    cfg = replace(cfg, exp=e, traffic=replace(cfg.traffic, real_csv_path=str(trace.resolve())))
    cells = len(e["balances"]) * len(e["scenarios"])
    coarse, full = candidates(e, True), candidates(e)
    validation = (1 + sum(min(e["shortlist_per_method"], sum(c["method"] == m for c in full))
                          for m in METHODS if m != "plain")) * e["validation_windows"] * len(seeds)
    return cfg, {"experiment": cfg.label, "anvil": resolve_anvil_bin(cfg.chain.anvil_bin),
                 "trace": str(trace), "balances": e["balances"], "scenarios": e["scenarios"],
                 "grids": {k: e[k] for k in bounds if k != "balances"},
                 "coarse_grids": {k: e["coarse_" + k] for k in ("epsilons", "windows", "half_lives")},
                 "cells": cells, "ctx_per_trial": n, "coarse_trials": cells * len(coarse),
                 "full_train_grid_trials": cells * len(full),
                 "refine_trials_upper_bound": cells * min(len(full) - len(coarse),
                     27 * e["shortlist_per_method"] + sum(len(coverage_probes(e, m)) for m in METHODS[2:])),
                 "validation_trials_upper_bound": cells * validation,
                 "validation_windows": e["validation_windows"], "validation_seeds": seeds,
                 "long_memory_caution": n / e["rate"] < e["warmup_arrival_blocks"] + 5 * max(e["windows"] + e["half_lives"]),
                 "warmup_arrival_blocks": e["warmup_arrival_blocks"],
                 "ports": [cfg.chain.base_port, cfg.chain.base_port + 15],
                 "note": "Bounded staged search, not an exhaustive optimum. Coarse/refine use one seed; validation is frozen, held out."}


class RunLock:
    def __init__(self, output):
        self.path = output / "active.lock"

    def __enter__(self):
        if self.path.exists():
            previous = read(self.path)
            try:
                p = psutil.Process(previous["pid"])
                if p.create_time() == previous["created"]:
                    raise RuntimeError(f"Search already running as PID {p.pid}")
            except psutil.NoSuchProcess:
                pass
            self.path.rename(self.path.with_name("stale_lock_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json"))
        with self.path.open("x", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "created": psutil.Process().create_time()}, f)
        return self

    def __exit__(self, *args):
        self.path.unlink(missing_ok=True)


def ensure_data(cfg, output, split):
    manifest = output / "workloads" / split / "manifest.json"
    if manifest.exists():
        data = read(manifest)
        for item in data["scenarios"].values():
            if sha256_file(output / item["path"]) != item["sha256"]:
                raise ValueError("Prepared workload modified; refusing to mix inputs")
        return data
    index = 0 if split == "train" else int(split.split("_")[1])
    count = cfg.exp["candidate_count"]
    print(f"PREPARE {split}: eligible candidate interval [{index * count}, {(index + 1) * count})", flush=True)
    pool, filters = candidate_pool(cfg, count, skip=index * count)
    n = cfg.scale.num_brokers * cfg.exp["ctx_per_broker"]
    data = {"split": split, "skip": index * count, "candidate_count": count,
            "filters": filters, "first_source_line": pool[0]["source_line"],
            "last_source_line": pool[-1]["source_line"], "candidate_digest": identity(pool), "scenarios": {}}
    for scenario in cfg.exp["scenarios"]:
        folder = manifest.parent / scenario["name"]
        path, audit = prepare_scenario(pool, scenario, cfg, n, folder)
        selected = read(path)["rows"]
        # Audit every budget, not just the template's placeholder 0.5 ETH.
        audit["amount_above_initial_balance_by_budget"] = {
            str(b): sum(r["amount_wei"] > int(float(b) * ETH) for r in selected) for b in cfg.exp["balances"]}
        save(folder / "workload_audit.json", audit)
        s = audit["selected_ctx"]
        data["scenarios"][scenario["name"]] = {"path": str(path.relative_to(output)),
            "sha256": audit["workload_sha256"], "peak_endpoint_share": s["peak_endpoint_share"],
            "net_imbalance_ratio": s["net_imbalance_ratio"], "intra_fraction": audit["selection"]["intra_fraction"]}
        print(f"  {scenario['name']}: {n} CTX, peak endpoint share={s['peak_endpoint_share']:.3%}", flush=True)
    save(manifest, data)
    return data


def pct95(values):
    values = sorted(values)
    return values[round(.95 * (len(values) - 1))] if values else None


def metrics_from_summary(summary, expected_tag, n, warmup_arrivals):
    d = read(summary)
    if not d.get("passed") or not d.get("status", {}).get("complete") or len(d.get("arms", [])) != 1:
        raise ValueError("Incomplete trial or failed integrity gates")
    a = d["arms"][0]
    if (a["arm"] != expected_tag or a["n"] != n or a["failed"] or not a.get("block_capacity")
            or not a["gates"] or not all(a["gates"].values()) or not a["burn_reconcile"]["ok"]
            or a["tdr"].get("transfers_lost", 0)):
        raise ValueError("Invalid trial identity, count, conservation or lost TDR transfer")
    with (summary.parent / expected_tag / "ctx_rows.csv").open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if (len(rows) != n or len({r["ctx_id"] for r in rows}) != n
            or {int(r["arrival_pos"]) for r in rows} != set(range(n))):
        raise ValueError("CTX CSV count, identity or arrival sequence mismatch")
    m = {"n": n, "relay": a["relayed"], "relay_fraction": a["relayed"] / n,
         "tdr_legs": a["legs"]["tdr_legs"], "tdr_legs_per_ctx": a["legs"]["tdr_legs"] / n,
         "tdr_events": a["tdr"]["events_opened"], "tdr_transfers": a["tdr"]["transfers_done"],
         "tdr_moved_eth": a["tdr"]["moved_wei"] / ETH,
         "throughput_ctx_per_s": a["throughput_ctx_per_s"], "e2e_p95_s": a["e2e_secs"]["p95"],
         "achieved_injection_ctx_per_s": a["coord"].get("achieved_injection_ctx_per_s"),
         "backlog_peak": a["coord"].get("backlog_peak"),
         "peak_full_block_fraction": a["block_capacity"]["max_full_block_fraction"],
         "peak_mean_gas_utilization": a["block_capacity"]["max_mean_utilization"]}
    for key in ("relay_fraction", "tdr_legs_per_ctx", "throughput_ctx_per_s", "e2e_p95_s"):
        if m[key] is None or not math.isfinite(m[key]):
            raise ValueError(f"Missing/nonfinite ranking metric {key}")
    for name, selected in (("cold", [r for r in rows if int(r["arrival_pos"]) < warmup_arrivals]),
                           ("post_warmup", [r for r in rows if int(r["arrival_pos"]) >= warmup_arrivals])):
        m[name + "_n"] = len(selected)
        m[name + "_relay_fraction"] = sum(r["route"] == "relay" for r in selected) / len(selected) if selected else None
        m[name + "_e2e_p95_s"] = pct95([float(r["e2e_secs"]) for r in selected if r["e2e_secs"]])
    return m


def stop_owned_tree(proc):
    if proc.poll() is not None:
        return
    try:
        parent = psutil.Process(proc.pid)
        owned = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for p in owned:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def execute_trial(cfg, output, trial, stage, dataset):
    folder = output / "trials" / identity(trial)
    folder.mkdir(parents=True, exist_ok=True)
    prior = sorted(folder.glob("attempt_*"))
    attempt = folder / f"attempt_{len(prior) + 1:03d}"
    attempt.mkdir()
    info = dataset["scenarios"][trial["scenario"]]
    child = trial_config(cfg, trial["candidate"], trial["balance"],
                         (output / info["path"]).resolve(), info["sha256"], trial["seed"])
    config_path = attempt / "config.yaml"
    config_path.write_text(yaml.safe_dump(to_params_dict(child), sort_keys=False), encoding="utf-8")
    record = {**trial, "trial_id": identity(trial), "stage": stage, "status": "running",
              "attempt": str(attempt.relative_to(output)), "workload_sha256": info["sha256"]}
    save(attempt / "result.json", record)
    command = [sys.executable, "-u", str(EXP003), "--config", str(config_path), "--out-root", str(attempt)]
    print(f"RUN {stage} {trial['scenario']} B={trial['balance']} {trial['candidate']} {trial['split']} seed={trial['seed']}", flush=True)
    proc = None
    try:
        with (attempt / "run.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace",
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            for line in proc.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            rc = proc.wait()
        summaries = sorted(attempt.glob("*/summary.json"))
        if rc or len(summaries) != 1:
            raise RuntimeError(f"Child returncode={rc}, summaries={len(summaries)}; inspect run.log")
        summary = summaries[0]
        n = cfg.scale.num_brokers * cfg.exp["ctx_per_broker"]
        record["metrics"] = metrics_from_summary(summary, arm(trial["candidate"], trial["balance"])[1],
                                                  n, cfg.exp["warmup_arrival_blocks"] * cfg.exp["rate"])
        record.update(status="complete", summary=str(summary.relative_to(output)), summary_sha256=sha256_file(summary))
    except BaseException as exc:
        if proc is not None:
            stop_owned_tree(proc)
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save(attempt / "result.json", record)
        save(folder / "result.json", record)
        raise
    finally:
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
    save(attempt / "result.json", record)
    save(folder / "result.json", record)
    return record


def get_records(output):
    records = {}
    for p in sorted((output / "trials").glob("*/result.json")):
        r = read(p)
        if r["status"] == "complete" and sha256_file(output / r["summary"]) != r["summary_sha256"]:
            raise ValueError(f"Completed summary modified: {p}")
        records[r["trial_id"]] = r
    return records


def make_plan(cfg, stage, scenario, balance, records):
    training = [r for r in records.values() if r["status"] == "complete" and r["split"] == "train"
                and r["scenario"] == scenario and r["balance"] == balance]
    k = cfg.exp["shortlist_per_method"]
    selected = []
    parents = []
    if stage == "coarse":
        selected = candidates(cfg.exp, True)
    elif stage == "refine":
        for method in ("proportional", "topup", "ewma"):
            seeds = shortlist([r for r in training if r["candidate"]["method"] == method], k)
            parents.extend(r["trial_id"] for r in seeds)
            selected.extend(c for r in seeds for c in neighbors(cfg.exp, r["candidate"]))
            selected.extend(coverage_probes(cfg.exp, method))
        already = {identity(r["candidate"]) for r in training}
        selected = [c for c in selected if identity(c) not in already]
    else:
        selected = [{"method": "plain"}]
        for method in METHODS[1:]:
            choices = shortlist([r for r in training if r["candidate"]["method"] == method], k)
            if not choices:
                raise ValueError(f"No training results for {method}")
            parents.extend(r["trial_id"] for r in choices)
            selected.extend(r["candidate"] for r in choices)
    selected = list({identity(c): c for c in selected}.values())
    splits = ["train"] if stage != "validate" else [f"validation_{i}" for i in range(1, cfg.exp["validation_windows"] + 1)]
    seeds = [cfg.exp["train_seed"]] if stage != "validate" else cfg.exp["validation_seeds"]
    trials = [{"scenario": scenario, "balance": float(balance), "candidate": c, "split": split, "seed": seed}
              for split in splits for seed in seeds for c in selected]
    random.Random(f"{cfg.exp['search_seed']}:{stage}:{scenario}:{balance}").shuffle(trials)
    return {"stage": stage, "scenario": scenario, "balance": balance, "parent_trials": parents,
            "selection": "Pareto layers and normalized crowding; fixed ID tie break", "trials": trials}


def report(output, records, status):
    rows = [{"trial_id": r["trial_id"], "stage": r["stage"], "scenario": r["scenario"],
             "balance": r["balance"], "split": r["split"], "seed": r["seed"], "status": r["status"],
             **r["candidate"], **r.get("metrics", {}), "error": r.get("error", "")}
            for r in records.values()]
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    complete = [r for r in records.values() if r["status"] == "complete"]
    frontiers = []
    for scenario, balance, method in sorted({(r["scenario"], r["balance"], r["candidate"]["method"]) for r in complete if r["split"] == "train"}):
        sample = [r for r in complete if r["split"] == "train" and r["scenario"] == scenario
                  and r["balance"] == balance and r["candidate"]["method"] == method]
        frontiers.append({"scenario": scenario, "balance": balance, "method": method,
                          "layers": [[r["trial_id"] for r in layer] for layer in pareto_layers(sample)]})
    validation = []
    for scenario, balance, cid in sorted({(r["scenario"], r["balance"], identity(r["candidate"])) for r in complete if r["split"] != "train"}):
        sample = [r for r in complete if r["split"] != "train" and r["scenario"] == scenario
                  and r["balance"] == balance and identity(r["candidate"]) == cid]
        by_window = {}
        for split in sorted({r["split"] for r in sample}):
            members = [r for r in sample if r["split"] == split]
            by_window[split] = {key: {"n": len(members), "median": statistics.median(r["metrics"][key] for r in members),
                "min": min(r["metrics"][key] for r in members), "max": max(r["metrics"][key] for r in members)}
                for key in ("relay_fraction", "tdr_legs_per_ctx", "throughput_ctx_per_s", "e2e_p95_s")}
        validation.append({"scenario": scenario, "balance": balance, "candidate": sample[0]["candidate"], "by_window": by_window})
    save(output / "report.json", {"status": status, "completed_trials": len(complete),
        "failed_trials": [r["trial_id"] for r in records.values() if r["status"] == "failed"],
        "training_frontiers": frontiers, "validation": validation,
        "note": "Partial reports are not completed searches. No guaranteed EWMA win; validation does not feed back into selection."})


class TrialBudgetReached(Exception):
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--scenarios")
    ap.add_argument("--balances", help="Comma-separated subset of configured balances")
    ap.add_argument("--stage", choices=(*STAGES, "all"), default="all")
    ap.add_argument("--check", "--dry-run", dest="check", action="store_true")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="Tiny, explicitly labeled five-method pipeline check; not search evidence")
    ap.add_argument("--resume", type=Path)
    ap.add_argument("--max-trials", type=int, default=0, help="New trials in this invocation; 0 means unlimited")
    args = ap.parse_args()
    if args.max_trials < 0:
        ap.error("--max-trials must be nonnegative")
    if args.resume:
        if args.overrides or args.scenarios or args.balances or args.smoke or args.config != str(HERE / "config.yaml"):
            ap.error("Resume uses frozen config; --set/--config/--scenarios/--balances cannot change it")
        output = args.resume.resolve()
        cfg, plan = resolve(load_config(output / "resolved.yaml"))
        frozen = read(output / "manifest.json")
        if (frozen["sources"] != sources() or frozen["trace"] != trace_stamp(cfg)
                or frozen["config_sha256"] != sha256_file(output / "resolved.yaml")):
            raise ValueError("Source code, frozen config or trace changed; start a new output directory")
    else:
        cfg = apply_overrides(load_config(args.config), args.overrides)
        if args.smoke:
            cfg = replace(cfg, label="exp013_parameter_search_smoke",
                chain=replace(cfg.chain, base_port=15600), scale=replace(cfg.scale, num_brokers=2),
                exp={**cfg.exp, "balances": [0.5], "valve_thresholds": [2.5],
                     "epsilons": [0.1], "windows": [1], "half_lives": [1],
                     "coarse_epsilons": [0.1], "coarse_windows": [1], "coarse_half_lives": [1],
                     "scenarios": [{"name": "three_hot6", "bits": 6, "hotspots": [0, 1, 2]}],
                     "shortlist_per_method": 1, "validation_windows": 1, "validation_seeds": [7],
                     "ctx_per_broker": 100, "candidate_count": 2000, "warmup_arrival_blocks": 1})
        cfg, plan = resolve(cfg, args.scenarios, args.balances)
        output = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.check:
        return 0
    if not args.resume:
        output.mkdir(parents=True)
        (output / "resolved.yaml").write_text(yaml.safe_dump(to_params_dict(cfg), sort_keys=False), encoding="utf-8")
        save(output / "manifest.json", {"plan": plan, "sources": sources(), "trace": trace_stamp(cfg),
                                       "config_sha256": sha256_file(output / "resolved.yaml")})
    print(f"OUTPUT: {output}", flush=True)
    with RunLock(output):
        records = get_records(output)
        if args.prepare_only:
            for split in ["train", *[f"validation_{i}" for i in range(1, cfg.exp["validation_windows"] + 1)]]:
                ensure_data(cfg, output, split)
            report(output, records, "prepared")
            return 0
        stages = STAGES if args.stage == "all" else (args.stage,)
        launched = 0
        datasets = {}
        try:
            for stage in stages:
                cells = [(s["name"], float(b)) for s in cfg.exp["scenarios"] for b in cfg.exp["balances"]]
                random.Random(f"{cfg.exp['search_seed']}:{stage}").shuffle(cells)
                for scenario, balance in cells:
                    cell = output / "plans" / scenario / str(balance)
                    for previous in STAGES[:STAGES.index(stage)]:
                        if not (cell / f"{previous}.complete.json").exists():
                            raise ValueError(f"{scenario} B={balance}: finish stage {previous} first")
                    path = cell / f"{stage}.json"
                    stage_plan = read(path) if path.exists() else make_plan(cfg, stage, scenario, balance, records)
                    if not path.exists():
                        save(path, stage_plan)  # Freeze adaptive choices before executing them.
                    for trial in stage_plan["trials"]:
                        tid = identity(trial)
                        if tid in records and records[tid]["status"] == "complete":
                            continue
                        if args.max_trials and launched >= args.max_trials:
                            raise TrialBudgetReached()
                        split = trial["split"]
                        if split not in datasets:
                            datasets[split] = ensure_data(cfg, output, split)
                        records[tid] = execute_trial(cfg, output, trial, stage, datasets[split])
                        launched += 1
                        report(output, records, f"running_{stage}")
                    save(cell / f"{stage}.complete.json", {"trial_ids": [identity(t) for t in stage_plan["trials"]]})
            report(output, records, "complete" if args.stage in ("all", "validate") else f"{args.stage}_complete")
        except TrialBudgetReached:
            report(output, records, "trial_budget_reached")
            print(f"Stopped at --max-trials. Resume: python {HERE / 'run.py'} --resume {output}", flush=True)
        except KeyboardInterrupt:
            report(output, get_records(output), "interrupted")
            return 130
        except Exception:
            report(output, get_records(output), "failed_or_blocked")
            raise
    print(f"REPORT: {output / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
