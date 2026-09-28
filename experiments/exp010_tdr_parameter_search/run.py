#!/usr/bin/env python3
"""exp010: resumable OAT, pairwise-grid and explicit Cartesian TDR search."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"
sys.path.insert(0, str(ROOT))
from brokerlab.config import load_config  # noqa: E402


@dataclass
class Trial:
    trial_id: str
    state: dict[str, Any]
    overrides: dict[str, Any]
    views: list[dict[str, Any]] = field(default_factory=list)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _tag(token: str) -> str:
    parts = token.split("@")
    tag = parts[0]
    if len(parts) > 1 and parts[1]:
        tag += f"@{float(parts[1])}"
    for raw in parts[2:7]:
        if raw:
            tag += f"@{float(raw)}"
    if len(parts) > 7 and parts[7]:
        tag += f"@p{int(round(float(parts[7]) * 100))}"
    return tag


def base_state(cfg) -> dict[str, Any]:
    e = cfg.exp
    return {
        "balance_eth": float(e["balances_eth"]),
        "valve_cap_mult": float(e["tdr_cap_mult"]),
        "proportional_epsilon": float(e["exp010_proportional_epsilon"]),
        "hard_epsilon": float(e["exp010_hard_epsilon"]),
        "hard_chi_blocks": int(e["exp010_hard_chi_blocks"]),
        "ewma_epsilon": float(e["exp010_ewma_epsilon"]),
        "ewma_chi_blocks": int(e["exp010_ewma_chi_blocks"]),
        "ewma_half_life": float(e["exp010_ewma_half_life"]),
        "ewma_min_blocks": int(e.get("exp010_ewma_min_blocks", 5)),
        "ewma_q_min": float(e["exp010_ewma_q_min"]),
    }


def apply_target(state: dict[str, Any], overrides: dict[str, Any],
                 target: str, value: Any) -> None:
    mapping = {
        "arm.all.balance_eth": "balance_eth",
        "arm.valve.cap_mult": "valve_cap_mult",
        "arm.proportional.epsilon": "proportional_epsilon",
        "arm.ewma.epsilon": "ewma_epsilon",
        "arm.ewma.chi_blocks": "ewma_chi_blocks",
        "arm.ewma.half_life": "ewma_half_life",
        "arm.ewma.min_blocks": "ewma_min_blocks",
        "arm.ewma.q_min": "ewma_q_min",
        "arm.hard.epsilon": "hard_epsilon",
        "arm.hard.chi_blocks": "hard_chi_blocks",
    }
    if target in mapping:
        key = mapping[target]
        current = state.get(key)
        # YAML commonly spells an integral float as `8`; keep signatures
        # semantic so 8 and 8.0 do not create duplicate trials/cache entries.
        if isinstance(current, float) and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            value = float(value)
        elif isinstance(current, int) and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            value = int(value)
        state[key] = value
    else:
        overrides[target] = value


def arm_tokens(state: dict[str, Any]) -> dict[str, str]:
    b = _fmt(state["balance_eth"])
    tokens = {
        "plain": f"plain@{b}",
        "valve": f"valve@{b}@{_fmt(state['valve_cap_mult'])}",
        # get() keeps manifests created before proportional epsilon became a
        # search dimension resumable and maps them to the historical 0.10.
        "proportional": f"tdr@{b}@@{_fmt(state.get('proportional_epsilon', 0.10))}",
        "hard_topup": (f"topup@{b}@@{_fmt(state['hard_epsilon'])}"
                       f"@{_fmt(state['hard_chi_blocks'])}"),
        "ewma_topup": (f"topup@{b}@@{_fmt(state['ewma_epsilon'])}"
                       f"@{_fmt(state['ewma_chi_blocks'])}"
                       f"@{_fmt(state['ewma_half_life'])}"
                       f"@{_fmt(state['ewma_q_min'])}"),
    }
    return tokens


def _signature(state: dict[str, Any], overrides: dict[str, Any]) -> str:
    raw = json.dumps({"state": state, "overrides": overrides},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _add_trial(store: dict[str, Trial], base: dict[str, Any],
               assignments: dict[str, Any], view: dict[str, Any],
               companion_state: dict[str, Any] | None = None,
               companion_overrides: dict[str, Any] | None = None) -> None:
    state = dict(base)
    overrides: dict[str, Any] = {}
    for target, value in (companion_state or {}).items():
        apply_target(state, overrides, target, value)
    overrides.update(companion_overrides or {})
    for target, value in assignments.items():
        apply_target(state, overrides, target, value)
    sig = _signature(state, overrides)
    if sig not in store:
        store[sig] = Trial(sig, state, overrides, [])
    store[sig].views.append(view)


def build_trials(cfg, space: dict[str, Any], profile_name: str,
                 stage: str = "all", only: set[str] | None = None) -> list[Trial]:
    if profile_name not in space["profiles"]:
        raise ValueError(f"unknown profile {profile_name!r}")
    profile = space["profiles"][profile_name]
    smoke = bool(profile.get("use_smoke_values", False))
    base = base_state(cfg)
    trials: dict[str, Trial] = {}
    _add_trial(trials, base, {}, {"kind": "baseline", "name": "baseline",
                                  "x_param": "baseline", "x_value": 0,
                                  "y_param": "", "y_value": ""})
    if stage in ("oat", "all"):
        for name, spec in space.get("dimensions", {}).items():
            if only and name not in only:
                continue
            values = spec.get("smoke_values") if smoke else spec.get("values")
            for value in values or []:
                _add_trial(
                    trials, base, {spec["target"]: value},
                    {"kind": "oat", "name": name,
                     "label": spec.get("label", name),
                     "x_param": spec["target"], "x_value": value,
                     "y_param": "", "y_value": ""},
                    spec.get("companion_state"),
                    spec.get("companion_overrides"))
    if stage in ("grid", "all") and profile.get("include_grids", False):
        for name, spec in space.get("grids", {}).items():
            if only and name not in only:
                continue
            xs, ys = list(spec["x_values"]), list(spec["y_values"])
            if smoke:
                xs = [xs[0], xs[len(xs)//2], xs[-1]]
                ys = [ys[0], ys[len(ys)//2], ys[-1]]
            for x, y in itertools.product(xs, ys):
                _add_trial(
                    trials, base, {spec["x_target"]: x, spec["y_target"]: y},
                    {"kind": "grid", "name": name,
                     "label": name, "x_param": spec["x_target"],
                     "x_label": spec.get("x_label", spec["x_target"]),
                     "x_value": x, "y_param": spec["y_target"],
                     "y_label": spec.get("y_label", spec["y_target"]),
                     "y_value": y}, spec.get("companion_state"),
                    spec.get("companion_overrides"))
    if stage == "cartesian":
        for name, spec in space.get("cartesian_sets", {}).items():
            if only and name not in only:
                continue
            keys, values = list(spec), list(spec.values())
            for combo in itertools.product(*values):
                assignments = dict(zip(keys, combo))
                _add_trial(
                    trials, base, assignments,
                    {"kind": "cartesian", "name": name,
                     "label": name, "x_param": "combination",
                     "x_value": json.dumps(assignments, sort_keys=True),
                     "y_param": "", "y_value": ""})
    return sorted(trials.values(), key=lambda t: t.trial_id)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_set_overrides(items: list[str] | None) -> dict[str, Any]:
    """Parse exp003-compatible KEY=VALUE overrides without guessing types here."""
    result: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set expects a non-empty key, got {item!r}")
        result[key] = value.strip()
    return result


def execution_fingerprint(config_path: Path) -> str:
    """Invalidate the arm cache whenever execution code/config changes."""
    paths = [config_path.resolve(), EXP003_RUN.resolve(), Path(__file__).resolve()]
    paths.extend(sorted((ROOT / "brokerlab").glob("*.py")))
    h = hashlib.sha256()
    for path in paths:
        h.update(str(path).encode("utf-8"))
        h.update(path.read_bytes())
    trace = Path(load_config(config_path).traffic.real_csv_path)
    h.update(str(trace.resolve()).encode("utf-8"))
    with trace.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def common_overrides(trial: Trial, profile: dict[str, Any], route_seed: int,
                     cli_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = dict(cli_overrides or {})
    overrides.update(trial.overrides)
    overrides.update({
        "exp.ctx_per_broker": int(profile["ctx_per_broker"]),
        "exp.route_seed": int(route_seed),
        # Fixed offered load: it is deliberately not a search dimension.
        "exp.rate": 120,
        "exp.tdr_ewma_min_blocks": int(trial.state.get("ewma_min_blocks", 5)),
        "exp.balances_eth": trial.state["balance_eth"],
        "broker.initial_balance_eth": trial.state["balance_eth"],
    })
    return overrides


def arm_cache_key(token: str, overrides: dict[str, Any], fingerprint: str) -> str:
    payload = {"fingerprint": fingerprint, "arm": token, "overrides": overrides}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def overrides_for_role(overrides: dict[str, Any], role: str) -> dict[str, Any]:
    """Drop EWMA-only settings from comparator cache keys and child configs."""
    result = dict(overrides)
    if role != "ewma_topup":
        result.pop("exp.tdr_ewma_min_blocks", None)
    return result


def _summary_complete(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(summary.get("status", {}).get("complete")) and bool(summary.get("arms"))


def _run_cached_arm(run_dir: Path, config_path: Path, token: str,
                    overrides: dict[str, Any], fingerprint: str
                    ) -> tuple[Path | None, int, bool]:
    """Run one arm or reuse its exact code/config/trace/seed-equivalent result."""
    key = arm_cache_key(token, overrides, fingerprint)
    cache_dir = run_dir / "arm_cache" / key
    cached_summary = cache_dir / "summary.json"
    if _summary_complete(cached_summary):
        print(f"  cache hit  {key}  {_tag(token)}", flush=True)
        return cached_summary, 0, True

    # Keep this path deliberately short.  exp003 appends another timestamp and
    # the full arm name; the former layout could exceed Windows' directory
    # length limit before an EWMA arm started running.
    attempt_root = run_dir / "a" / key / datetime.now().strftime("%H%M%S_%f")
    attempt_root.mkdir(parents=True, exist_ok=True)
    arm_overrides = dict(overrides)
    arm_overrides["exp.arms"] = token
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(config_path),
           "--out-root", str(attempt_root)]
    for key_name, value in arm_overrides.items():
        cmd += ["--set", f"{key_name}={_fmt(value)}"]
    write_json(cache_dir / "command.json", {
        "argv": cmd, "overrides": arm_overrides, "fingerprint": fingerprint,
        "cache_key": key,
    })
    print(f"  cache miss {key}  {_tag(token)}", flush=True)
    rc = subprocess.call(cmd)
    subdirs = sorted(p for p in attempt_root.iterdir() if p.is_dir())
    source = subdirs[-1] / "summary.json" if subdirs else None
    if rc == 0 and source is not None and _summary_complete(source):
        summary = json.loads(source.read_text(encoding="utf-8"))
        write_json(cached_summary, summary)
        write_json(cache_dir / "metadata.json", {
            "cache_key": key, "arm": token, "source_summary": str(source),
            "created_at": datetime.now().isoformat(), "fingerprint": fingerprint,
        })
        return cached_summary, rc, False
    return source, rc, False


def merge_arm_summaries(paths: list[Path], required_arms: list[str],
                        destination: Path) -> bool:
    """Build the five-arm summary consumed by the existing aggregation code."""
    docs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    arms = [arm for doc in docs for arm in doc.get("arms", [])]
    present = [arm.get("arm") for arm in arms]
    gates = {}
    arm_failures = {}
    for arm in arms:
        tag = arm["arm"]
        for name, ok in (arm.get("gates") or {}).items():
            gates[f"{tag}:{name}"] = bool(ok)
        burn_ok = bool((arm.get("burn_reconcile") or {}).get("ok"))
        gates[f"{tag}:burn_reconcile"] = burn_ok
        if not all((arm.get("gates") or {}).values()) or not burn_ok:
            arm_failures[tag] = "gate failure"
    complete = (len(arms) == len(required_arms)
                and set(present) == set(required_arms)
                and all(_summary_complete(path) for path in paths))
    merged = dict(docs[0])
    merged.update({
        "arms": arms,
        "gates": gates,
        "status": {
            "complete": complete, "aborted": False, "error": None,
            "required_arms": required_arms, "present_arms": present,
            "missing_arms": sorted(set(required_arms) - set(present)),
            "aborted_arms": [], "gates_ok": bool(gates) and all(gates.values()),
        },
        "arm_failures": arm_failures,
        "passed": complete and bool(gates) and all(gates.values()),
        "hypotheses": {},
        "cache": {"merged": True, "sources": [str(path) for path in paths]},
    })
    write_json(destination, merged)
    return complete


def read_manifest(path: Path) -> list[Trial]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [Trial(**item) for item in doc["trials"]]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


RAW_FIELDS = [
    "trial_id", "replicate", "route_seed", "role", "arm", "engine",
    "fund_eth", "n", "served", "relayed", "failed", "relay_rate",
    "events", "transfers", "relay_plus_events", "relay_plus_transfers",
    "moved_eth", "tdr_legs_share",
    "achieved_injection_ctx_per_s", "throughput_ctx_per_s",
    "full_wall_throughput_ctx_per_s", "wall_s",
    "close_tail_s", "e2e_p50_s", "e2e_p95_s", "gates_ok", "burn_ok",
    "demand_observed_total", "liquidity_updates",
]


def collect_records(run_dir: Path, trials: list[Trial]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for trial in trials:
        status_file = run_dir / "trials" / trial.trial_id / "status.json"
        if not status_file.exists():
            continue
        status = json.loads(status_file.read_text(encoding="utf-8"))
        roles = {_tag(v): k for k, v in arm_tokens(trial.state).items()}
        for rep in status.get("replicates", []):
            sp = Path(rep["summary_path"]) if rep.get("summary_path") else None
            if not sp or not sp.exists():
                continue
            summary = json.loads(sp.read_text(encoding="utf-8"))
            for arm in summary.get("arms", []):
                n = int(arm.get("n", 0))
                wall = float(arm.get("wall_s", 0) or 0)
                relayed = int(arm["relayed"])
                events = int(arm["tdr"]["events_opened"])
                transfers = int(arm["tdr"]["transfers_done"])
                row = {
                    "trial_id": trial.trial_id,
                    "replicate": rep["replicate"],
                    "route_seed": rep["route_seed"],
                    "role": roles.get(arm["arm"], arm["engine"]),
                    "arm": arm["arm"], "engine": arm["engine"],
                    "fund_eth": arm["fund_eth"], "n": n,
                    "served": arm["served"], "relayed": relayed,
                    "failed": arm["failed"],
                    "relay_rate": relayed / n if n else None,
                    "events": events, "transfers": transfers,
                    "relay_plus_events": relayed + events,
                    "relay_plus_transfers": relayed + transfers,
                    "moved_eth": arm["tdr"]["moved_wei"] / 1e18,
                    "tdr_legs_share": arm["legs"]["tdr_share_of_ctx_legs"],
                    "achieved_injection_ctx_per_s": arm["coord"].get(
                        "achieved_injection_ctx_per_s"),
                    "throughput_ctx_per_s": arm["throughput_ctx_per_s"],
                    "full_wall_throughput_ctx_per_s": n / wall if wall else None,
                    "wall_s": wall, "close_tail_s": arm.get("tdr_close_tail_s"),
                    "e2e_p50_s": arm["e2e_secs"]["p50"],
                    "e2e_p95_s": arm["e2e_secs"]["p95"],
                    "gates_ok": bool(arm["gates"]) and all(arm["gates"].values()),
                    "burn_ok": bool(arm["burn_reconcile"]["ok"]),
                    "demand_observed_total": arm["tdr"].get("demand_observed_total"),
                    "liquidity_updates": arm["coord"].get("liquidity_updates"),
                }
                records.append(row)
    return records


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def aggregate_records(run_dir: Path, trials: list[Trial]) -> None:
    records = collect_records(run_dir, trials)
    _write_csv(run_dir / "raw_results.csv", RAW_FIELDS, records)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault((row["trial_id"], row["role"]), []).append(row)
    by_id = {t.trial_id: t for t in trials}
    aggregate_rows: list[dict[str, Any]] = []
    metrics = ["n", "relayed", "relay_rate", "events", "transfers",
               "relay_plus_events", "relay_plus_transfers", "moved_eth",
               "tdr_legs_share", "achieved_injection_ctx_per_s",
               "throughput_ctx_per_s",
               "full_wall_throughput_ctx_per_s", "e2e_p50_s", "e2e_p95_s"]
    for (trial_id, role), rows in grouped.items():
        base = {"trial_id": trial_id, "role": role,
                "replicates": len(rows),
                "all_gates_ok": all(r["gates_ok"] and r["burn_ok"] for r in rows)}
        for metric in metrics:
            vals = [float(r[metric]) for r in rows if r.get(metric) is not None]
            base[f"{metric}_median"] = median(vals)
            base[f"{metric}_min"] = min(vals) if vals else None
            base[f"{metric}_max"] = max(vals) if vals else None
        base["state_json"] = json.dumps(by_id[trial_id].state, sort_keys=True)
        base["overrides_json"] = json.dumps(by_id[trial_id].overrides, sort_keys=True)
        views = by_id[trial_id].views or [{"kind": "unknown", "name": "unknown"}]
        for view in views:
            aggregate_rows.append({**base, **view})
    agg_fields = sorted({k for r in aggregate_rows for k in r})
    _write_csv(run_dir / "aggregate.csv", agg_fields, aggregate_rows)

    comparisons: list[dict[str, Any]] = []
    trial_roles: dict[str, dict[str, dict[str, Any]]] = {}
    for row in aggregate_rows:
        if row.get("kind") == "baseline" or row.get("name"):
            trial_roles.setdefault(row["trial_id"], {})[row["role"]] = row
    for trial in trials:
        roles = trial_roles.get(trial.trial_id, {})
        ew, va, pl = roles.get("ewma_topup"), roles.get("valve"), roles.get("plain")
        if not ew:
            continue
        for view in trial.views:
            row = {"trial_id": trial.trial_id, **view,
                   "ewma_relay": ew.get("relayed_median"),
                   "ewma_events": ew.get("events_median"),
                   "ewma_transfers": ew.get("transfers_median"),
                   "ewma_relay_plus_transfers": ew.get(
                       "relay_plus_transfers_median"),
                   "ewma_moved_eth": ew.get("moved_eth_median"),
                   "ewma_full_throughput": ew.get("full_wall_throughput_ctx_per_s_median"),
                   "all_gates_ok": all(item.get("all_gates_ok")
                                       for item in roles.values())}
            if va:
                row.update({"valve_relay": va.get("relayed_median"),
                            "valve_events": va.get("events_median"),
                            "valve_transfers": va.get("transfers_median"),
                            "valve_relay_plus_transfers": va.get(
                                "relay_plus_transfers_median"),
                            "valve_moved_eth": va.get("moved_eth_median"),
                            "ewma_minus_valve_relay": (float(ew["relayed_median"])
                                - float(va["relayed_median"])),
                            "ewma_beats_valve": (float(ew["relayed_median"])
                                < float(va["relayed_median"]))})
                if float(va.get("relayed_median") or 0) > 0:
                    row["ewma_reduction_vs_valve"] = ((float(va["relayed_median"])
                        - float(ew["relayed_median"])) / float(va["relayed_median"]))
            if pl and float(pl.get("relayed_median") or 0) > 0:
                row["ewma_reduction_vs_plain"] = ((float(pl["relayed_median"])
                    - float(ew["relayed_median"])) / float(pl["relayed_median"]))
                if float(pl.get("full_wall_throughput_ctx_per_s_median") or 0) > 0:
                    row["ewma_full_throughput_loss_vs_plain"] = ((
                        float(pl["full_wall_throughput_ctx_per_s_median"])
                        - float(ew["full_wall_throughput_ctx_per_s_median"]))
                        / float(pl["full_wall_throughput_ctx_per_s_median"]))
            comparisons.append(row)
    five_way: list[dict[str, Any]] = []
    role_order = ("plain", "valve", "proportional", "hard_topup", "ewma_topup")
    for trial in trials:
        roles = trial_roles.get(trial.trial_id, {})
        if len(roles) != len(role_order):
            continue
        row = {
            "trial_id": trial.trial_id,
            "views_json": json.dumps(trial.views, sort_keys=True),
            "state_json": json.dumps(trial.state, sort_keys=True),
            "overrides_json": json.dumps(trial.overrides, sort_keys=True),
            "all_gates_ok": all(roles[role]["all_gates_ok"] for role in role_order),
            "n_per_arm": roles["plain"].get("n_median"),
        }
        for role in role_order:
            item = roles[role]
            for source, suffix in (("relayed", "relay"), ("events", "events"),
                                   ("transfers", "transfers"),
                                   ("relay_plus_events", "relay_plus_events"),
                                   ("relay_plus_transfers", "relay_plus_transfers")):
                row[f"{role}_{suffix}"] = item.get(f"{source}_median")
        for suffix in ("relay", "events", "transfers", "relay_plus_transfers"):
            values = {role: _number(row[f"{role}_{suffix}"]) for role in role_order}
            available = {role: value for role, value in values.items()
                         if value is not None}
            if available:
                minimum = min(available.values())
                row[f"{suffix}_winners"] = ",".join(
                    role for role, value in available.items() if value == minimum)
        row["informative_load"] = (
            (_number(row["plain_relay"]) or 0) >= max(
                20, 0.02 * (_number(row["n_per_arm"]) or 0))
            and any((_number(row[f"{role}_events"]) or 0) > 0
                    for role in role_order[1:]))
        row["ewma_unique_relay_winner"] = row.get("relay_winners") == "ewma_topup"
        row["ewma_unique_combined_winner"] = (
            row.get("relay_plus_transfers_winners") == "ewma_topup")
        row["ewma_relay_savings_vs_valve"] = (
            float(row["valve_relay"]) - float(row["ewma_topup_relay"]))
        row["ewma_combined_savings_vs_valve"] = (
            float(row["valve_relay_plus_transfers"])
            - float(row["ewma_topup_relay_plus_transfers"]))
        five_way.append(row)
    five_fields = sorted({key for row in five_way for key in row})
    _write_csv(run_dir / "five_way_comparisons.csv", five_fields, five_way)

    def rank_num(value):
        return float(value) if value is not None else float("inf")

    five_by_id = {row["trial_id"]: row for row in five_way}
    informative_ids = {row["trial_id"] for row in five_way
                       if row["informative_load"] and row["all_gates_ok"]}
    for row in comparisons:
        row["informative_load"] = row["trial_id"] in informative_ids
        five = five_by_id.get(row["trial_id"], {})
        row["ewma_unique_relay_winner"] = five.get("ewma_unique_relay_winner")
        row["ewma_unique_combined_winner"] = five.get(
            "ewma_unique_combined_winner")
    comp_fields = sorted({k for r in comparisons for k in r})
    _write_csv(run_dir / "comparisons.csv", comp_fields, comparisons)
    leaderboard = sorted(
        comparisons,
        key=lambda r: (r["trial_id"] not in informative_ids,
                       not bool(r.get("ewma_unique_relay_winner")),
                       not bool(r.get("ewma_beats_valve")),
                       rank_num(r.get("ewma_relay")),
                       rank_num(r.get("ewma_relay_plus_transfers")),
                       rank_num(r.get("ewma_transfers")),
                       rank_num(r.get("ewma_moved_eth"))))
    leaderboard = [{"rank": i, **row} for i, row in enumerate(leaderboard, 1)]
    lb_fields = sorted({k for r in leaderboard for k in r})
    _write_csv(run_dir / "leaderboard.csv", lb_fields, leaderboard)

    candidates = [r for r in comparisons if r["trial_id"] in informative_ids
                  and r.get("all_gates_ok")
                  and r.get("ewma_relay") is not None]
    pareto = []
    for row in candidates:
        point = (float(row["ewma_relay"]), float(row["ewma_transfers"]),
                 float(row["ewma_moved_eth"]))
        dominated = False
        for other in candidates:
            if other is row:
                continue
            op = (float(other["ewma_relay"]), float(other["ewma_transfers"]),
                  float(other["ewma_moved_eth"]))
            if all(a <= b for a, b in zip(op, point)) and any(
                    a < b for a, b in zip(op, point)):
                dominated = True
                break
        if not dominated:
            pareto.append(row)
    pareto_fields = sorted({k for r in pareto for k in r})
    _write_csv(run_dir / "pareto_front.csv", pareto_fields, pareto)
    write_parameter_analysis(run_dir, aggregate_rows)


PARAMETER_ROLE = {
    "epsilon": "ewma_topup",
    "ewma_half_life": "ewma_topup",
    "ewma_min_blocks": "ewma_topup",
    "ewma_q_min": "ewma_topup",
    "broker_balance_eth": "ewma_topup",
    "window_blocks": "hard_topup",
    "valve_cap_mult": "valve",
    "proportional_epsilon": "proportional",
    "hard_epsilon": "hard_topup",
    "value_floor_eth": "ewma_topup",
    "value_cap_eth": "ewma_topup",
}
SCENARIO_PARAMETERS = {"broker_balance_eth", "value_floor_eth", "value_cap_eth"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _spread(values: list[float]) -> float | None:
    return max(values) - min(values) if values else None


def _impact_level(value: float) -> str:
    """Classify an absolute rate span; thresholds are percentage points."""
    tolerance = 1e-12
    if value >= 0.10 - tolerance:
        return "large"
    if value >= 0.03 - tolerance:
        return "medium"
    if value >= 0.01 - tolerance:
        return "small"
    return "negligible"


def write_parameter_analysis(run_dir: Path,
                             aggregate_rows: list[dict[str, Any]]) -> None:
    """Write transparent OAT value tables, sensitivity ranking and best settings."""
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    view_meta: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in aggregate_rows:
        if row.get("kind") != "oat":
            continue
        key = (str(row["trial_id"]), str(row["name"]), str(row.get("x_value")))
        grouped.setdefault(key, {})[str(row["role"])] = row
        view_meta[key] = row

    values: list[dict[str, Any]] = []
    for key, roles in grouped.items():
        meta = view_meta[key]
        name = str(meta["name"])
        target_role = PARAMETER_ROLE.get(name, "ewma_topup")
        target = roles.get(target_role)
        if not target:
            continue
        out: dict[str, Any] = {
            "trial_id": key[0], "parameter": name,
            "parameter_label": meta.get("label", name),
            "parameter_target": meta.get("x_param"),
            "value": meta.get("x_value"),
            "parameter_kind": ("scenario_sensitivity" if name in SCENARIO_PARAMETERS
                               else "policy_parameter"),
            "target_role": target_role,
            "all_gates_ok": all(item.get("all_gates_ok") for item in roles.values()),
            "target_replicates": target.get("replicates"),
            "target_relay_rate_min": target.get("relay_rate_min"),
            "target_relay_rate_max": target.get("relay_rate_max"),
        }
        for role in ("plain", "valve", "proportional", "hard_topup", "ewma_topup"):
            item = roles.get(role)
            if not item:
                continue
            prefix = role.replace("_topup", "")
            for source, suffix in (
                ("relayed_median", "relay"), ("relay_rate_median", "relay_rate"),
                ("events_median", "events"),
                ("transfers_median", "transfers"), ("moved_eth_median", "moved_eth"),
                ("relay_plus_events_median", "relay_plus_events"),
                ("relay_plus_transfers_median", "relay_plus_transfers"),
                ("tdr_legs_share_median", "tdr_legs_share"),
                ("achieved_injection_ctx_per_s_median", "injection_ctx_per_s"),
                ("throughput_ctx_per_s_median", "completion_ctx_per_s"),
                ("e2e_p50_s_median", "e2e_p50_s"),
                ("e2e_p95_s_median", "e2e_p95_s"),
            ):
                out[f"{prefix}_{suffix}"] = item.get(source)
        for baseline in ("plain", "valve"):
            base_rate = _number(out.get(f"{baseline}_relay_rate"))
            ewma_rate = _number(out.get("ewma_relay_rate"))
            if base_rate is not None and ewma_rate is not None:
                out[f"ewma_relay_rate_improvement_vs_{baseline}"] = base_rate - ewma_rate
                out[f"ewma_relative_reduction_vs_{baseline}"] = (
                    (base_rate - ewma_rate) / base_rate if base_rate > 0 else None)
        role_rates = {role: _number(out.get(f"{role.replace('_topup', '')}_relay_rate"))
                      for role in ("plain", "valve", "proportional",
                                   "hard_topup", "ewma_topup")}
        comparable = {k: v for k, v in role_rates.items() if v is not None}
        if comparable:
            minimum = min(comparable.values())
            out["relay_winner"] = ",".join(sorted(k for k, v in comparable.items()
                                                    if v == minimum))
        target_prefix = target_role.replace("_topup", "")
        plain_n = _number(roles.get("plain", {}).get("n_median")) or 0
        out["informative_load"] = (
            (_number(out.get("plain_relay")) or 0) >= max(20, 0.02 * plain_n)
            and any((_number(out.get(f"{role}_events")) or 0) > 0
                    for role in ("valve", "proportional", "hard", "ewma")))
        for suffix in ("relay", "relay_rate", "events", "transfers",
                       "relay_plus_events", "relay_plus_transfers", "moved_eth",
                       "tdr_legs_share", "injection_ctx_per_s",
                       "completion_ctx_per_s", "e2e_p50_s", "e2e_p95_s"):
            out[f"target_{suffix}"] = out.get(f"{target_prefix}_{suffix}")
        values.append(out)

    value_fields = sorted({key for row in values for key in row})
    _write_csv(run_dir / "parameter_value_impacts.csv", value_fields, values)

    effects: list[dict[str, Any]] = []
    for name in sorted({str(row["parameter"]) for row in values}):
        rows = [row for row in values if row["parameter"] == name
                and bool(row.get("all_gates_ok"))]
        if not rows:
            continue
        relay_rates = [_number(r.get("target_relay_rate")) for r in rows]
        relay_rates = [v for v in relay_rates if v is not None]
        transfer_rates = []
        for row in rows:
            transfers = _number(row.get("target_transfers"))
            # Every search arm has 1,000 CTX; read n from aggregate when available.
            aggregate_n = next((_number(x.get("n_median")) for x in aggregate_rows
                                if x.get("trial_id") == row["trial_id"]
                                and x.get("role") == row["target_role"]), None)
            if transfers is not None and aggregate_n:
                transfer_rates.append(transfers / aggregate_n)
        moved = [_number(r.get("target_moved_eth")) for r in rows]
        moved = [v for v in moved if v is not None]
        throughput = [_number(r.get("target_completion_ctx_per_s")) for r in rows]
        throughput = [v for v in throughput if v is not None]
        advantages = [_number(r.get("ewma_relay_rate_improvement_vs_valve"))
                      for r in rows]
        advantages = [v for v in advantages if v is not None]
        replicate_counts = [_number(r.get("target_replicates")) for r in rows]
        replicate_counts = [int(v) for v in replicate_counts if v is not None]
        within_value_noise = []
        for row in rows:
            low = _number(row.get("target_relay_rate_min"))
            high = _number(row.get("target_relay_rate_max"))
            if low is not None and high is not None:
                within_value_noise.append(high - low)
        relay_span = _spread(relay_rates) or 0.0
        transfer_span = _spread(transfer_rates) or 0.0
        overall = max(relay_span, transfer_span)
        min_replicates = min(replicate_counts) if replicate_counts else 0
        max_noise = max(within_value_noise) if min_replicates >= 2 and within_value_noise else None
        effect_to_noise = ((relay_span / max_noise) if max_noise and max_noise > 0
                           else None)
        valid_best = [r for r in rows
                      if r["informative_load"]
                      and (_number(r.get("target_events")) or 0) > 0
                      and _number(r.get("target_relay_rate")) is not None]
        best = min(valid_best, key=lambda r: (
            _number(r.get("target_relay_rate")) or 0.0,
            _number(r.get("target_relay_plus_transfers")) or 0.0,
            _number(r.get("target_moved_eth")) or 0.0,
            -(_number(r.get("target_completion_ctx_per_s")) or 0.0),
        )) if valid_best else {}
        effects.append({
            "parameter": name, "parameter_label": rows[0]["parameter_label"],
            "parameter_kind": rows[0]["parameter_kind"],
            "target_role": rows[0]["target_role"],
            "values_tested": len(rows),
            "informative_values": sum(bool(r["informative_load"]) for r in rows),
            "best_value_lexicographic": best.get("value"),
            "best_target_relay_rate": best.get("target_relay_rate"),
            "best_target_events": best.get("target_events"),
            "best_target_transfers": best.get("target_transfers"),
            "best_target_relay_plus_transfers": best.get(
                "target_relay_plus_transfers"),
            "best_target_moved_eth": best.get("target_moved_eth"),
            "relay_rate_min": min(relay_rates) if relay_rates else None,
            "relay_rate_max": max(relay_rates) if relay_rates else None,
            "relay_rate_span": relay_span,
            "rebalancing_rate_span": transfer_span,
            "moved_eth_span": _spread(moved),
            "completion_throughput_span": _spread(throughput),
            "ewma_advantage_vs_valve_min": min(advantages) if advantages else None,
            "ewma_advantage_vs_valve_max": max(advantages) if advantages else None,
            "overall_effect_rate": overall,
            "impact_level": _impact_level(overall),
            "minimum_replicates": min_replicates,
            "max_within_value_relay_noise": max_noise,
            "relay_effect_to_noise_ratio": effect_to_noise,
            "evidence_quality": ("replicated" if min_replicates >= 3 else "exploratory"),
            "selection_note": ("sensitivity only; do not treat an easier workload as "
                               "an algorithm optimum" if name in SCENARIO_PARAMETERS else
                               "no eligible trial: require plain relay >= 2% and >= 20 "
                               "and target TDR events > 0" if not valid_best else
                               "best informative value uses relay, relay+transfers, "
                               "moved ETH, then throughput"),
        })
    effects.sort(key=lambda row: float(row["overall_effect_rate"]), reverse=True)
    for rank, row in enumerate(effects, 1):
        row["impact_rank"] = rank
    effect_fields = sorted({key for row in effects for key in row})
    _write_csv(run_dir / "parameter_effects.csv", effect_fields, effects)

    recommended = [row for row in effects
                   if row["parameter_kind"] == "policy_parameter"
                   and row["best_value_lexicographic"] is not None]
    recommendation_fields = [
        "parameter", "parameter_label", "target_role", "best_value_lexicographic",
        "best_target_relay_rate", "best_target_events", "best_target_transfers",
        "best_target_relay_plus_transfers", "best_target_moved_eth",
        "impact_rank", "impact_level", "selection_note",
        "minimum_replicates", "evidence_quality",
    ]
    _write_csv(run_dir / "recommended_policy_values.csv",
               recommendation_fields, recommended)
    write_json(run_dir / "analysis_summary.json", {
        "method": {
            "effect": "max(relay-rate span, rebalancing-transfers-per-CTX span)",
            "impact_thresholds": {"large": 0.10, "medium": 0.03,
                                  "small": 0.01, "negligible": 0.0},
            "best_value_order": ["relay_rate asc", "relay_plus_transfers asc",
                                 "moved_eth asc", "completion_throughput desc"],
            "informative_load_rule": "plain relay >= 2% and >= 20 CTX; a TDR policy triggered",
            "recommendation_rule": "target policy TDR events > 0; no recommendation if none qualifies",
            "scenario_parameters": sorted(SCENARIO_PARAMETERS),
        },
        "parameter_count": len(effects),
        "ranking": effects,
    })


def run_trial(run_dir: Path, config_path: Path, trial: Trial,
              profile: dict[str, Any], replicates: int,
              cli_overrides: dict[str, Any] | None = None,
              fingerprint: str | None = None) -> dict[str, Any]:
    trial_dir = run_dir / "trials" / trial.trial_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    status_file = trial_dir / "status.json"
    status = (json.loads(status_file.read_text(encoding="utf-8"))
              if status_file.exists() else
              {"trial_id": trial.trial_id, "replicates": []})
    done = {int(r["replicate"]) for r in status["replicates"]
            if r.get("complete") and r.get("summary_path")
            and Path(r["summary_path"]).exists()}
    tokens = arm_tokens(trial.state)
    roles = {role: _tag(token) for role, token in tokens.items()}
    write_json(trial_dir / "trial.json", {**asdict(trial), "arm_tokens": tokens,
                                           "arm_tags": roles})
    for rep in range(1, replicates + 1):
        if rep in done:
            continue
        rep_root = trial_dir / f"replicate_{rep}"
        rep_root.mkdir(parents=True, exist_ok=True)
        route_seed = 7 + (rep - 1) * 10
        print(f"\n[{trial.trial_id}] replicate {rep}/{replicates}", flush=True)
        overrides = common_overrides(trial, profile, route_seed, cli_overrides)
        fp = fingerprint or execution_fingerprint(config_path)
        source_paths: list[Path] = []
        returncodes: list[int] = []
        hits = 0
        for role, token in tokens.items():
            arm_overrides = overrides_for_role(overrides, role)
            source, rc, hit = _run_cached_arm(
                run_dir, config_path, token, arm_overrides, fp)
            returncodes.append(rc)
            hits += int(hit)
            if source is None or not _summary_complete(source):
                print(f"  arm failed: {role} ({_tag(token)}), rc={rc}", flush=True)
                break
            source_paths.append(source)
        summary_path = rep_root / "summary.json"
        complete = (len(source_paths) == len(tokens)
                    and merge_arm_summaries(source_paths,
                                            [_tag(t) for t in tokens.values()],
                                            summary_path))
        write_json(rep_root / "command.json", {
            "mode": "content-addressed-arm-cache", "overrides": overrides,
            "arm_tokens": tokens, "cache_hits": hits,
            "cache_misses": len(source_paths) - hits, "fingerprint": fp,
        })
        status["replicates"] = [r for r in status["replicates"]
                                if int(r["replicate"]) != rep]
        status["replicates"].append({
            "replicate": rep, "route_seed": route_seed,
            "returncode": max(returncodes) if returncodes else 1,
            "result_dir": str(rep_root),
            "summary_path": str(summary_path) if summary_path.exists() else None,
            "cache_hits": hits, "cache_misses": len(source_paths) - hits,
            "complete": complete,
        })
        completed = [r for r in status["replicates"] if r.get("complete")]
        status["complete"] = (len(completed) >= replicates
                              and all(r.get("complete") for r in status["replicates"]))
        write_json(status_file, status)
    return status


def estimate_arm_runs(run_dir: Path, trials: list[Trial], profile: dict[str, Any],
                      replicates: int, cli_overrides: dict[str, Any],
                      fingerprint: str) -> tuple[int, int]:
    keys: set[str] = set()
    missing = 0
    for trial in trials:
        for rep in range(1, replicates + 1):
            route_seed = 7 + (rep - 1) * 10
            overrides = common_overrides(trial, profile, route_seed, cli_overrides)
            for role, token in arm_tokens(trial.state).items():
                key = arm_cache_key(token, overrides_for_role(overrides, role), fingerprint)
                if key not in keys:
                    keys.add(key)
                    if not _summary_complete(run_dir / "arm_cache" / key / "summary.json"):
                        missing += 1
    return len(keys), missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=HERE / "config.yaml")
    ap.add_argument("--space", type=Path, default=HERE / "search_space.yaml")
    ap.add_argument("--profile", choices=("smoke", "search", "broad", "full"),
                    default=None)
    ap.add_argument("--stage", choices=("baseline", "oat", "grid", "cartesian", "all"),
                    default="oat")
    ap.add_argument("--only", help="comma-separated dimension/grid/cartesian names")
    ap.add_argument("--replicates", type=int)
    ap.add_argument("--max-trials", type=int)
    ap.add_argument("--resume", type=Path)
    ap.add_argument("--output-root", type=Path,
                    help="root for new runs; defaults to this experiment's out directory")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="pass a common override to every arm (for example chain.base_port=9800)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    space = yaml.safe_load(args.space.read_text(encoding="utf-8"))
    fingerprint = execution_fingerprint(args.config)
    cli_overrides = parse_set_overrides(args.set)
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None

    if args.resume:
        run_dir = args.resume.resolve()
        saved = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        if saved.get("execution_fingerprint") != fingerprint:
            raise SystemExit(
                "--resume execution fingerprint differs from the manifest; "
                "the code/config/trace changed. Start a new output directory "
                "to avoid mixing different workloads in one search.")
        profile_name = args.profile or saved["profile"]
        profile = dict(space["profiles"][profile_name])
        profile["ctx_per_broker"] = int(saved["ctx_per_broker"])
        replicates = args.replicates or int(saved["replicates"])
        saved_overrides = dict(saved.get("cli_overrides") or {})
        saved_overrides.update(cli_overrides)
        cli_overrides = saved_overrides
        all_trials = read_manifest(run_dir / "manifest.json")
        stage_name = saved.get("stage", args.stage)
    else:
        profile_name = args.profile or str(cfg.exp.get("exp010_profile", "broad"))
        profile = space["profiles"][profile_name]
        replicates = args.replicates or int(profile["replicates"])
        all_trials = build_trials(cfg, space, profile_name, args.stage, only)
        stage_name = args.stage
        output_root = args.output_root.resolve() if args.output_root else HERE / "out"
        run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    pending = []
    for trial in all_trials:
        status_file = run_dir / "trials" / trial.trial_id / "status.json"
        complete = False
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8"))
            completed = [r for r in status.get("replicates", []) if r.get("complete")]
            complete = len(completed) >= replicates
        if not complete:
            pending.append(trial)
    trials_to_run = pending
    if args.max_trials is not None:
        trials_to_run = pending[:max(0, args.max_trials)]
    unique_arm_runs, missing_arm_runs = estimate_arm_runs(
        run_dir, trials_to_run, profile, replicates, cli_overrides, fingerprint)
    arm_runs_upper = len(trials_to_run) * replicates * 5
    print(f"exp010 profile={profile_name} stage={stage_name} "
          f"unique_trials={len(all_trials)} pending={len(pending)} "
          f"selected={len(trials_to_run)} replicates={replicates} "
          f"arm_runs_max={arm_runs_upper} unique_arm_runs={unique_arm_runs} "
          f"cache_misses={missing_arm_runs}")
    if args.dry_run:
        for trial in trials_to_run[:20]:
            print(trial.trial_id, trial.views[0], trial.state, trial.overrides)
        if len(trials_to_run) > 20:
            print(f"... {len(trials_to_run) - 20} more selected trials")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    if not (run_dir / "manifest.json").exists():
        write_json(run_dir / "manifest.json", {
            "created_at": datetime.now().isoformat(), "profile": profile_name,
            "stage": stage_name, "replicates": replicates,
            "ctx_per_broker": profile["ctx_per_broker"],
            "total_ctx_per_arm": int(profile["ctx_per_broker"]) * int(cfg.scale.num_brokers),
            "fixed_rate": 120, "cli_overrides": cli_overrides,
            "execution_fingerprint": fingerprint,
            "config": str(args.config.resolve()), "space": str(args.space.resolve()),
            "trials": [asdict(t) for t in all_trials],
        })
    failures = 0
    for index, trial in enumerate(trials_to_run, 1):
        print(f"\n=== trial {index}/{len(trials_to_run)} {trial.trial_id} ===", flush=True)
        status = run_trial(run_dir, args.config.resolve(), trial, profile, replicates,
                           cli_overrides, fingerprint)
        if not status.get("complete"):
            failures += 1
        aggregate_records(run_dir, all_trials)
    if not trials_to_run:
        aggregate_records(run_dir, all_trials)
    if not args.no_plot:
        subprocess.call([sys.executable, str(HERE / "plot_results.py"),
                         "--run", str(run_dir)])
    print(f"\nresults: {run_dir}\nfailed/incomplete trials: {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
