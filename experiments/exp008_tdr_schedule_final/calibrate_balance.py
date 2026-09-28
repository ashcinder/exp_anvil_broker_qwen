#!/usr/bin/env python3
"""Eight paired balance pilots, transparent selection, then two formal exp008 rounds.

No chains are started on import or with --dry-run. Each pilot uses fresh chains
through exp003; the formal run uses the existing exp008 runner unchanged.
"""
import argparse
import copy
import csv
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab.config import load_config, to_params_dict

BALANCES = (0.005, 0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5)
VALVE_CAP_MULT = 10.0
DESIGN_NOTE = (
    "Deliberately high-threshold Valve stress control (cap_mult=10.0), "
    "not a fair best-tuned Valve comparison or an empirically proven worst threshold. "
    "Lower trigger frequency may reduce TDR cost; no guaranteed EWMA win. "
    "Previous cap_mult=1.3 results remain unchanged and must not be relabeled."
)
METHODS = ("plain", "valve", "proportional", "hard_window", "ewma")
PILOT_CTX = 5000
FORMAL_CTX = 40000
RULE = (
    "Eligible: plain Relay >= 2% and EWMA TDR events > 0. "
    "Rank by (best alternative Relay - EWMA Relay)/N descending, "
    "then EWMA Relay+successful TDR transfers ascending, then balance ascending. "
    "A nonpositive winning margin is NOT an EWMA win. "
    "No eligible candidate: stop without launching formal rounds."
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def configured(base, balance, *, pilot):
    data = copy.deepcopy(base)
    exp = data["exp"]
    b = str(float(balance))
    n = PILOT_CTX if pilot else FORMAL_CTX
    brokers = int(data["scale"]["num_brokers"])
    if brokers <= 0 or n % brokers:
        raise ValueError(f"{n} CTX must be divisible by num_brokers")
    exp.update(balances_eth=float(balance), ctx_per_broker=n // brokers,
               exp008_valve_sweep=False,  # Legacy balance search must not activate the new Valve grid.
               tdr_cap_mult=VALVE_CAP_MULT,
               exp008_design_note=DESIGN_NOTE,
               route_seed=107 if pilot else 7, exp008_sessions=1 if pilot else 2,
               arms=f"plain@{b},valve@{b}@{VALVE_CAP_MULT},tdr@{b}@@0.1,"
                    f"topup@{b}@@0.1,topup@{b}@@0.95@@20",
               exp008_final_arm=f"topup@{b}@0.95@20.0")
    data["broker"]["initial_balance_eth"] = float(balance)
    return data


def expected_tags(balance):
    b = str(float(balance))
    return (f"plain@{b}", f"valve@{b}@{VALVE_CAP_MULT}", f"tdr@{b}@0.1",
            f"topup@{b}@0.1", f"topup@{b}@0.95@20.0")


def read_trial(path, balance):
    summary = json.loads(Path(path).read_text(encoding="utf-8"))
    arms = summary["arms"]
    tags = expected_tags(balance)
    if (not summary.get("passed") or not summary.get("status", {}).get("complete")
            or len(arms) != 5 or {a["arm"] for a in arms} != set(tags)):
        raise RuntimeError(f"Incomplete or invalid pilot: {path}")
    by_tag = {a["arm"]: a for a in arms}
    rows = []
    for method, tag in zip(METHODS, tags):
        arm = by_tag[tag]
        if (arm["n"] != PILOT_CTX or arm["failed"] != 0
                or arm.get("fund_eth") != balance
                or not arm.get("gates") or not all(arm["gates"].values())
                or not arm["burn_reconcile"]["ok"]
                or arm["tdr"].get("transfers_lost", 0) != 0):
            raise RuntimeError(f"Invalid count, gate or lost transfer: {path}, {tag}")
        relay = int(arm["relayed"])
        transfers = int(arm["tdr"]["transfers_done"])
        rows.append(dict(balance_eth=balance, method=method, arm=tag, n=arm["n"],
                         valve_cap_mult=VALVE_CAP_MULT if method == "valve" else "",
                         comparison_design="high_threshold_valve_stress_control",
                         relay=relay, relay_rate=relay / arm["n"],
                         tdr_events=int(arm["tdr"]["events_opened"]),
                         tdr_transfers=transfers, relay_plus_transfers=relay + transfers,
                         tdr_chain_legs=arm["legs"]["tdr_legs"],
                         moved_eth=int(arm["tdr"]["moved_wei"]) / 10**18,
                         throughput_ctx_per_s=arm["throughput_ctx_per_s"],
                         injection_ctx_per_s=arm["coord"]["achieved_injection_ctx_per_s"],
                         summary=str(path)))
    return rows


def rank_candidates(rows):
    ranking = []
    for balance in sorted({r["balance_eth"] for r in rows}):
        group = {r["method"]: r for r in rows if r["balance_eth"] == balance}
        if set(group) != set(METHODS):
            raise ValueError(f"Missing methods for {balance}")
        ewma, plain = group["ewma"], group["plain"]
        other = min(r["relay"] for m, r in group.items() if m != "ewma")
        ranking.append(dict(balance_eth=balance,
                            eligible=plain["relay_rate"] >= 0.02 and ewma["tdr_events"] > 0,
                            relay_margin=(other - ewma["relay"]) / ewma["n"],
                            ewma_beats_all_relay=ewma["relay"] < other,
                            ewma_relay=ewma["relay"], best_other_relay=other,
                            ewma_relay_plus_transfers=ewma["relay_plus_transfers"]))
    return sorted(ranking, key=lambda r: (not r["eligible"], -r["relay_margin"],
                                         r["ewma_relay_plus_transfers"], r["balance_eth"]))


def write_csv(path, rows):
    if rows:
        with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def check_ports(base):
    start = base["chain"]["base_port"]
    for port in range(start, start + base["chain"]["num_shards"]):
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} occupied; finish the other experiment first.")


def call_logged(command, logfile):
    print(f"Starting: {subprocess.list2cmdline(command)}\nLog: {logfile}", flush=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with Path(logfile).open("w", encoding="utf-8") as out:
        return subprocess.call(command, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--pilot-only", action="store_true",
                        help="Select and save formal config, but do not launch formal rounds")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only; no writes, chain starts or experiments")
    args = parser.parse_args(argv)
    config = args.config.resolve()
    original = config.read_bytes()
    base = to_params_dict(load_config(config))
    plan = dict(balances_eth=BALANCES, pilot_ctx_per_method=PILOT_CTX,
                formal_ctx_per_method_per_round=FORMAL_CTX, formal_rounds=2,
                pilot_route_seed=107, formal_route_seeds=[7, 17],
                pilot_method_runs=40, formal_method_runs=10,
                total_ctx=600000, rule=RULE,
                valve_cap_mult=VALVE_CAP_MULT, comparison_design=DESIGN_NOTE,
                traffic=base["traffic"], rate=base["exp"]["rate"],
                source_config=str(config), source_sha256=sha256(config))
    print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
    if args.dry_run:
        return 0
    check_ports(base)
    job = HERE / "calibration" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    job.mkdir(parents=True)
    (job / "config.before.yaml").write_bytes(original)
    write_json(job / "manifest.json", plan)
    source_files = [Path(__file__), HERE / "run.py",
                    HERE.parent / "exp003_tdr_on_off/run.py",
                    *sorted((ROOT / "brokerlab").glob("*.py"))]
    write_json(job / "source_hashes.json", {str(p): sha256(p) for p in source_files})
    status = dict(state="pilot", job=str(job), completed_balances=[], pid=os.getpid())
    rows = []
    try:
        for balance in BALANCES:
            status.update(current_balance=balance)
            write_json(job / "status.json", status)
            trial = job / f"balance_{balance:g}"
            trial.mkdir()
            cfg = trial / "config.yaml"
            cfg.write_text(yaml.safe_dump(configured(base, balance, pilot=True),
                                          allow_unicode=True, sort_keys=False), encoding="utf-8")
            load_config(cfg)
            check_ports(base)
            command = [sys.executable, "-u", str(HERE.parent / "exp003_tdr_on_off/run.py"),
                       "--config", str(cfg), "--out-root", str(trial / "out")]
            write_json(trial / "command.json", command)
            rc = call_logged(command, trial / "run.log")
            summaries = list((trial / "out").glob("*/summary.json"))
            if rc != 0 or len(summaries) != 1:
                raise RuntimeError(f"Pilot {balance} failed, rc={rc}; see {trial / 'run.log'}")
            rows.extend(read_trial(summaries[0], balance))
            write_csv(job / "pilot_metrics.csv", rows)
            status["completed_balances"].append(balance)
            print(f"Pilot {balance:g} ETH complete ({len(rows)}/40 method runs)", flush=True)
        ranking = rank_candidates(rows)
        write_csv(job / "balance_ranking.csv", ranking)
        selected = next((r for r in ranking if r["eligible"]), None)
        write_json(job / "selection.json", dict(rule=RULE, ranking=ranking, selected=selected,
                   caveat=DESIGN_NOTE + " Exploratory selection on a trace prefix; not independent validation or pure EWMA ablation."))
        if selected is None:
            status.update(state="no_informative_candidate")
            print("No informative balance; formal run NOT started. Inspect all pilot results.", flush=True)
            return 2
        chosen = configured(base, selected["balance_eth"], pilot=False)
        chosen["exp"]["exp008_design_note"] = (
            f"{DESIGN_NOTE} "
            f"Selected from 8 paired 5000-CTX balance pilots: {job}. {RULE} "
            "Amount filter 0.001..1 ETH on ETH_cleaned.csv; contract endpoints excluded. "
            "Formal seeds 7/17 differ, but pilot is a trace prefix, not a time-window holdout. "
            "EWMA and hard-window epsilon differ: not a pure estimator ablation. "
            f"Pilot EWMA strictly best Relay: {selected['ewma_beats_all_relay']}.")
        formal = job / "formal.selected.yaml"
        formal.write_text(yaml.safe_dump(chosen, allow_unicode=True, sort_keys=False), encoding="utf-8")
        load_config(formal)
        # Do not overwrite user edits made while the pilots were running.
        if config.read_bytes() != original:
            raise RuntimeError(f"Config changed during pilots; not overwritten. Selected config: {formal}")
        config.write_bytes(formal.read_bytes())
        status.update(state="selected", selected=selected, selected_config=str(formal))
        write_json(job / "status.json", status)
        print(f"Selected: {selected}\nConfig: {formal}", flush=True)
        if args.pilot_only:
            return 0
        check_ports(base)
        previous = set((HERE / "out").glob("*/report.json"))
        status.update(state="formal")
        write_json(job / "status.json", status)
        command = [sys.executable, "-u", str(HERE / "run.py"), "--config", str(formal)]
        write_json(job / "formal.command.json", command)
        rc = call_logged(command, job / "formal.log")
        reports = set((HERE / "out").glob("*/report.json")) - previous
        matched = []
        for report in reports:
            result = json.loads(report.read_text(encoding="utf-8"))
            if result.get("params", {}).get("exp", {}).get("exp008_design_note") == chosen["exp"]["exp008_design_note"]:
                matched.append((report, result))
        if len(matched) != 1 or not matched[0][1].get("status", {}).get("complete"):
            raise RuntimeError(f"Formal experiment incomplete, rc={rc}; see {job / 'formal.log'}")
        report, result = matched[0]
        status.update(state="complete" if result["verdict_all"] else "complete_hypothesis_not_met",
                      formal_report=str(report), formal_returncode=rc,
                      verdict_all=result["verdict_all"])
        print(f"Formal results: {report}\nState: {status['state']}", flush=True)
        return 0 if result["verdict_all"] else 1
    except BaseException as exc:
        status.update(state="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        write_json(job / "status.json", status)


if __name__ == "__main__":
    sys.exit(main())
