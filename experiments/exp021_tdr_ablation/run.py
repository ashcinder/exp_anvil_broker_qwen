#!/usr/bin/env python3
"""Experiment 21: paired 2x2 TDR ablation on the exp008 clean workload.

Only the TDR policy (proportional/top-up) and demand estimator (hard/EWMA)
vary. All four TDR arms use epsilon=0.95. Plain is an overall TDR-off reference.
The primary score is Relay-routed CTXs + completed TDR transfer requests;
the latter are *not* customer CTXs or individual on-chain transaction legs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"
sys.path.insert(0, str(ROOT))

from brokerlab.config import load_config, to_params_dict  # noqa: E402
from experiments.exp003_tdr_on_off.run import _arm_spec, _arm_tag  # noqa: E402


# The first arm is a reference, not a cell of the factorial ablation.
DESIGN = (
    ("plain", "plain@150", "TDR off", None, None),
    ("prop_hard", "tdr@150@@0.95", "Proportional + hard window", "proportional", 0.0),
    ("prop_ewma", "tdr@150@@0.95@@20", "Proportional + EWMA", "proportional", 20.0),
    ("topup_hard", "topup@150@@0.95", "Top-up + hard window", "topup", 0.0),
    ("full", "topup@150@@0.95@@20", "Top-up + EWMA (full)", "topup", 20.0),
)
EXPECTED_TOTAL = 80_000
EXPECTED_EPSILON = 0.95


def validate_design(cfg) -> list[dict]:
    """Fail before starting Anvil if the experiment stops being comparable."""
    exp = cfg.exp
    checks = {
        "label": (cfg.label, "exp021_tdr_ablation"),
        "exp.exp021_ctx_total": (exp.get("exp021_ctx_total"), EXPECTED_TOTAL),
        "exp.ctx_per_broker": (exp.get("ctx_per_broker"), 1600),
        "exp.rate": (exp.get("rate"), 120),
        "exp.pool_scan": (exp.get("pool_scan"), 250000),
        "exp.group_cap_mult": (exp.get("group_cap_mult"), 2.0),
        "exp.seed": (exp.get("seed"), 42),
        "exp.route_seed": (exp.get("route_seed"), 7),
        "exp.balances_eth": (exp.get("balances_eth"), 150),
        "broker.initial_balance_eth": (cfg.broker.initial_balance_eth, 150),
        "chain.num_shards": (cfg.chain.num_shards, 16),
        "scale.num_brokers": (cfg.scale.num_brokers, 50),
        "traffic.kind": (cfg.traffic.kind, "real_csv"),
        "traffic.allow_contract_endpoints": (cfg.traffic.allow_contract_endpoints, False),
        "traffic.value_floor_eth": (cfg.traffic.value_floor_eth, 0.01),
        "traffic.value_cap_eth": (cfg.traffic.value_cap_eth, 10.0),
        "exp.tdr_epsilon": (exp.get("tdr_epsilon"), EXPECTED_EPSILON),
        "exp.tdr_ewma_half_life": (exp.get("tdr_ewma_half_life"), 0.0),
        "exp.tdr_q_min": (exp.get("tdr_q_min"), 0.10),
        "exp.tdr_window_blocks": (exp.get("tdr_window_blocks"), 20),
        "exp.tdr_chi_blocks": (exp.get("tdr_chi_blocks"), 10),
        "exp.tdr_trigger_mode": (exp.get("tdr_trigger_mode"), "excess_only"),
        "b2e.enabled": (cfg.b2e.enabled, False),
    }
    for name, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(f"{name} must match exp008 template: {expected!r}; got {actual!r}")
    if cfg.scale.num_brokers * int(exp["ctx_per_broker"]) != EXPECTED_TOTAL:
        raise ValueError("The per-arm workload must be exactly 80,000 CTXs")
    if Path(cfg.traffic.real_csv_path).name != "ETH_cleaned.csv":
        raise ValueError("exp021 must use trace/ETH_cleaned.csv")
    if not Path(cfg.traffic.real_csv_path).is_file():
        raise FileNotFoundError(f"Clean trace not found: {cfg.traffic.real_csv_path}")
    if "tdr_surplus_epsilon" in exp and not math.isclose(
            float(exp["tdr_surplus_epsilon"]), EXPECTED_EPSILON):
        raise ValueError("top-up surplus epsilon must also be 0.95")
    # With both at zero, proportional and top-up use the same demand target;
    # their policy is the only intended difference in the 2x2 design.
    if float(exp.get("tdr_target_cap", 0.0)) != 0.0:
        raise ValueError("tdr_target_cap must remain disabled in this ablation")
    if float(exp.get("tdr_min_reserve_eth", 0.0)) != 0.0:
        raise ValueError("tdr_min_reserve_eth must remain disabled in this ablation")

    tokens = [t.strip() for t in str(exp.get("arms", "")).split(",") if t.strip()]
    if tokens != [entry[1] for entry in DESIGN]:
        raise ValueError("exp.arms must be the fixed five-arm exp021 factorial design")
    arms = []
    for key, token, label, policy, expected_hl in DESIGN:
        spec = _arm_spec(token)
        tag = _arm_tag(spec)
        _, engine, fund, _cap, eps, _chi, hl, _qmin, frac = spec
        effective_hl = exp["tdr_ewma_half_life"] if hl is None else hl
        if (engine != (policy or "plain") or fund != 150 or frac is not None
                or (policy is not None and (eps != EXPECTED_EPSILON
                                             or effective_hl != expected_hl))):
            raise ValueError(f"Invalid ablation arm token: {token}")
        arms.append({"key": key, "token": token, "tag": tag, "label": label,
                     "policy": policy, "epsilon": eps if policy else None,
                     "ewma_half_life_blocks": expected_hl})
    if len({arm["tag"] for arm in arms}) != len(arms):
        raise ValueError("Arm tags are not unique")
    return arms


def _csv_counts(arm_dir: Path) -> tuple[int, int, int]:
    n = relay = transfers = 0
    with (arm_dir / "ctx_rows.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            n += 1
            if str(row["ok"]).lower() not in {"true", "1"}:
                raise ValueError(f"Unsuccessful CTX in {arm_dir}")
            relay += row["route"] == "relay"
    with (arm_dir / "tdr_moves.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            transfers += row["state"] == "done"
    return n, int(relay), int(transfers)


def summarize(session_dir: Path, arms: list[dict]) -> dict:
    summary_path = session_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected = {a["tag"] for a in arms}
    actual = {str(a["arm"]) for a in summary.get("arms", [])}
    if not summary.get("passed") or expected != actual:
        raise ValueError(f"exp003 session failed or arms differ: expected={expected}, actual={actual}")
    if int(summary["params"]["_runtime"]["total_ctx"]) != EXPECTED_TOTAL:
        raise ValueError("Session workload differs from 80,000 CTXs per arm")
    by_tag = {str(a["arm"]): a for a in summary["arms"]}
    results = []
    for design in arms:
        item = by_tag[design["tag"]]
        n, relay, transfers = _csv_counts(session_dir / design["tag"])
        if (n != EXPECTED_TOTAL or n != int(item["n"])
                or relay != int(item["relayed"])
                or transfers != int(item["tdr"]["transfers_done"])):
            raise ValueError(f"CSV/summary count mismatch: {design['tag']}")
        if (int(item.get("failed", 0)) != 0
                or item.get("coord", {}).get("aborted")
                or not item.get("gates") or not all(item["gates"].values())
                or not item.get("burn_reconcile", {}).get("ok", False)):
            raise ValueError(f"Integrity or conservation gate failed: {design['tag']}")
        results.append({**design, "ctx_count": n, "relay_ctx": relay,
                        "tdr_transfers_done": transfers,
                        "relay_plus_tdr_transfers": relay + transfers,
                        "tdr_events_opened": int(item["tdr"]["events_opened"]),
                        "tdr_moved_eth": int(item["tdr"]["moved_wei"]) / 1e18,
                        "throughput_ctx_per_s": float(item["throughput_ctx_per_s"]),
                        "gates_all_true": True, "burn_reconcile_ok": True})
    scores = {r["key"]: r["relay_plus_tdr_transfers"] for r in results}
    full = scores["full"]
    return {
        "status": "complete",
        "session_summary": str(summary_path),
        "metric_definition": "relay-routed customer CTXs + completed TDR transfer requests; not total customer CTXs or on-chain transaction legs",
        "arms": results,
        "comparisons": {
            "remove_topup_keep_ewma": scores["prop_ewma"] - full,
            "remove_ewma_keep_topup": scores["topup_hard"] - full,
            "remove_both": scores["prop_hard"] - full,
            "tdr_off": scores["plain"] - full,
            "factorial_interaction": full - scores["topup_hard"] - scores["prop_ewma"] + scores["prop_hard"],
        },
        "interpretation_limit": "One paired session is descriptive; no statistical significance claim.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the five-arm design without launching Anvil")
    parser.add_argument("--output", type=Path,
                        help="new output directory (default: exp021/out/<timestamp>)")
    args = parser.parse_args()
    cfg = load_config(args.config)
    arms = validate_design(cfg)
    print(f"exp021 | clean trace | {EXPECTED_TOTAL:,} CTXs per arm | "
          f"{len(arms)} arms x 1 session = {EXPECTED_TOTAL * len(arms):,} CTX executions")
    print("Primary score: Relay CTXs + completed TDR transfers (not total customer CTXs)")
    for arm in arms:
        print(f"  {arm['key']:>10}  {arm['tag']:<30} {arm['label']}")
    if args.dry_run:
        print("Dry run: no chain launched and no output written.")
        return 0

    out_dir = args.output or HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    session_root = out_dir / "session_1"
    session_root.mkdir()
    command = [sys.executable, str(EXP003_RUN), "--config", str(args.config.resolve()),
               "--out-root", str(session_root)]
    started = time.monotonic()
    completed = subprocess.run(command, check=False)
    candidates = sorted(p for p in session_root.iterdir()
                        if p.is_dir() and (p / "summary.json").is_file())
    report = {"experiment": "exp021_tdr_ablation", "params": to_params_dict(cfg),
              "ctx_per_arm": EXPECTED_TOTAL, "sessions": 1,
              "subprocess_returncode": completed.returncode,
              "elapsed_seconds": round(time.monotonic() - started, 1),
              "source_experiment": "exp008_tdr_schedule_final",
              "exp003_session": str(candidates[-1]) if candidates else None}
    try:
        if completed.returncode != 0 or len(candidates) != 1:
            raise RuntimeError("exp003 failed or did not produce exactly one complete session")
        report.update(summarize(candidates[0], arms))
        with (out_dir / "ablation_metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            fields = ["key", "tag", "label", "policy", "epsilon", "ewma_half_life_blocks",
                      "ctx_count", "relay_ctx", "tdr_transfers_done", "relay_plus_tdr_transfers",
                      "tdr_events_opened", "tdr_moved_eth", "throughput_ctx_per_s"]
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(report["arms"])
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report.update({"status": "failed", "error": str(exc)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(f"exp021 report: {out_dir / 'report.json'}")
    if report["status"] != "complete":
        print(f"exp021 FAILED: {report['error']}")
        return 1
    for arm in report["arms"]:
        print(f"  {arm['label']:<29} Relay {arm['relay_ctx']:>7,} + "
              f"TDR {arm['tdr_transfers_done']:>7,} = "
              f"{arm['relay_plus_tdr_transfers']:>7,}")
    print("One session only: descriptive ablation, not a significance test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
