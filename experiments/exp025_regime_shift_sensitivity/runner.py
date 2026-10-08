"""Serial, resumable experiments with verified raw outputs and plot compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brokerlab.config import apply_overrides, load_config, to_params_dict
from brokerlab.procmon import RSSWatchdog
from experiments.exp025_regime_shift_sensitivity import pipeline
from experiments.exp025_regime_shift_sensitivity.paper_data import export_arm_data, read_csv, validate_arm, write_csv


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint():
    files = [Path(__file__), Path(pipeline.__file__), Path(__file__).with_name("paper_data.py")]
    files += sorted((ROOT / "brokerlab").glob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def token_tag(token):
    name, _, fund, cap, eps, chi, hl, q_min, frac = pipeline._arm_spec(token)
    tag = f"{name}@{fund}" if fund else name
    for value in (cap, eps, chi, hl, q_min):
        if value is not None:
            tag += f"@{value}"
    if frac is not None:
        tag += f"@p{int(round(frac*100))}"
    return tag


def tasks_for(cfg, kind, space=None):
    tasks = []
    rounds = int(cfg.exp["repetitions"])
    if kind == "exp025":
        if not space or set(space) - {"q_min", "half_life", "epsilon"}:
            raise ValueError("Sensitivity space must contain supported dimensions")
        for parameter, spec in space.items():
            values = spec["values"]
            if not values or len(set(values)) != len(values):
                raise ValueError(f"Empty or duplicate grid values: {parameter}")
    for rep in range(1, rounds + 1):
        seed = int(cfg.exp["route_seed"]) + (rep - 1) * int(cfg.exp["route_seed_step"])
        settings = []
        if kind == "exp025":
            for parameter, spec in space.items():
                for value in spec["values"]:
                    state = dict(q_min=0.3, half_life=20, epsilon=0.95)
                    state.update(spec["fixed"])
                    state[parameter] = value
                    if not 0 <= state["q_min"] <= 1 or not 0 <= state["epsilon"] <= 1 or state["half_life"] <= 0:
                        raise ValueError(f"Invalid sensitivity setting: {state}")
                    fund = cfg.exp["balances_eth"]
                    token = f"topup@{fund}@@{state['epsilon']}@@{state['half_life']}@{state['q_min']}"
                    settings.append((parameter, value, token))
        else:
            settings = [("figures", name.split("@")[0], name)
                        for name in cfg.exp["arms"].split(",")]
        for parameter, value, token in settings:
            slug = f"{parameter}_{value}_r{rep}"
            tasks.append(dict(id=slug, sweep=parameter, value=value, round=rep,
                              route_seed=seed, token=token, tag=token_tag(token), status="pending"))
    return tasks


def free_port_block(preferred, count):
    for base in range(preferred, 49152-count, count):
        probes = []
        try:
            for port in range(base, base+count):
                probe = socket.socket()
                probes.append(probe)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
            return base
        except OSError:
            pass
        finally:
            for probe in probes:
                probe.close()
    raise RuntimeError("No free contiguous Anvil port block")


def task_cfg(cfg, task):
    return apply_overrides(cfg, [f"exp.route_seed={task['route_seed']}", f"exp.arms={task['token']}"])


def load_completed(run, task, workload, validate=True, workload_hash=None):
    summary_path = run / task.get("summary", "tasks/_missing/summary.json")
    if not summary_path.is_file():
        return None
    doc = json.loads(summary_path.read_text(encoding="utf-8"))
    if doc.get("task_id") != task["id"] or doc.get("workload_sha256") != (workload_hash or digest(workload)):
        raise ValueError(f"Saved task identity/workload mismatch: {task['id']}")
    stats = doc["arms"][0]
    if stats["arm"] != task["tag"] or doc["params"]["exp"]["route_seed"] != task["route_seed"]:
        raise ValueError("Saved arm/route seed mismatch")
    if validate:
        validate_arm(stats, summary_path.parent / task["tag"], workload)
    return stats


def assemble(run, manifest, cfg, workload):
    records, sessions = [], []
    workload_hash = digest(workload)
    for task in manifest["tasks"]:
        if task["status"] != "complete":
            continue
        stats = load_completed(run, task, workload, validate=False, workload_hash=workload_hash)
        if stats is None:
            raise ValueError("Completed task has no summary")
        records.append(dict(task_id=task["id"], sweep=task["sweep"], value=task["value"],
                            round=task["round"], route_seed=task["route_seed"],
                            n=stats["n"], served=stats["served"], relayed=stats["relayed"],
                            service_ratio=stats["served"]/stats["n"],
                            transfers=stats["tdr"]["transfers_done"],
                            events=stats["tdr"]["events_opened"], moved_eth=stats["tdr"]["moved_wei"]/10**18,
                            throughput=stats["throughput_ctx_per_s"], gates_ok=True, burn_ok=True,
                            summary=task["summary"]))
    if records:
        write_csv(run / "raw_results.csv", records, list(records[0]))
    complete = len(records) == len(manifest["tasks"])
    if manifest["experiment"] == "exp026":
        for rep in range(1, int(cfg.exp["repetitions"])+1):
            current = [t for t in manifest["tasks"] if t["round"] == rep]
            if not all(t["status"] == "complete" for t in current):
                continue
            directory = (run / current[0]["summary"]).parent
            docs = [json.loads((run/t["summary"]).read_text(encoding="utf-8")) for t in current]
            write_json(directory / "summary.json", dict(params=docs[0]["params"], arms=[d["arms"][0] for d in docs],
                                                          status={"complete": True}))
            sessions.append(dict(index=rep, path=str(directory.relative_to(run)), route_seed=current[0]["route_seed"]))
    report = dict(experiment=manifest["experiment"], params=to_params_dict(cfg),
                  status={"complete": complete, "expected_arms": list(dict.fromkeys(t["tag"] for t in manifest["tasks"]))},
                  session_runs=sessions, total_ctx_per_arm=len(workload), repetitions=cfg.exp["repetitions"],
                  workload_sha256=workload_hash, completed_tasks=len(records), planned_tasks=len(manifest["tasks"]),
                  raw_results=records,
                  accounting="Artificial destination rotation on selected ETH_cleaned trace; control-message bytes + signed Relay burn and receipt bytes. Profit cost is an explicit nominal model; chain gasPrice=0.")
    write_json(run / "report.json", report)
    return complete


def main(here, kind):
    parser = argparse.ArgumentParser(description=f"{kind}: regime-shift experiment and compatible paper data")
    parser.add_argument("--config", type=Path, default=here/"config.yaml")
    parser.add_argument("--space", type=Path, default=here/"search_space.yaml")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--only", help="exp025 dimension: q_min, half_life, epsilon (comma separated)")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--output-root", type=Path, default=here/"out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="80 CTX, 1 broker, 1 round; outputs labeled smoke")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), args.set)
    space = yaml.safe_load(args.space.read_text(encoding="utf-8")) if kind == "exp025" else None
    if args.only:
        if kind != "exp025":
            raise ValueError("--only applies to sensitivity dimensions")
        names = args.only.split(",")
        if set(names) - set(space):
            raise ValueError(f"Unknown dimension: {names}")
        space = {name: space[name] for name in names}
    if args.smoke:
        cfg = replace(cfg, scale=replace(cfg.scale, num_brokers=1), broker=replace(cfg.broker, initial_balance_eth=5))
        cfg = apply_overrides(cfg, ["exp.ctx_per_broker=80", "exp.repetitions=1", "exp.rate=5",
                                   "exp.balances_eth=5", "exp.drain_tail_s=240"])
        if space:
            space = {key: {**spec, "values": spec["values"][:1]} for key, spec in space.items()}
        if kind == "exp026":
            cfg = apply_overrides(cfg, ["exp.arms=plain@5,topup@5@@0.95@@20@0.3", "exp.profit_broker_idx=0"])
    expected = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    required_ctx = 40000 if kind == "exp025" else 80000
    required_rounds = 10 if kind == "exp025" else 1
    if not args.smoke and (expected != required_ctx or int(cfg.exp["repetitions"]) != required_rounds):
        raise ValueError(f"Formal {kind} requires {required_ctx} CTX per arm and {required_rounds} rounds; use --smoke for checks")
    tasks = tasks_for(cfg, kind, space)
    signature = digest(dict(params=to_params_dict(cfg), space=space, kind=kind, smoke=args.smoke))
    print(f"{kind}: {len(tasks)} arm runs x {expected} CTX = {len(tasks)*expected:,}; serial; smoke={args.smoke}", flush=True)
    if args.dry_run:
        print(json.dumps(dict(config=str(args.config), space=space, planned_tasks=len(tasks),
                              rounds=cfg.exp["repetitions"], ctx_per_arm=expected, first_task=tasks[0]), indent=2))
        return 0
    run = args.resume.resolve() if args.resume else args.output_root.resolve()/datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if args.resume:
        manifest = json.loads((run/"manifest.json").read_text(encoding="utf-8"))
        if manifest["signature"] != signature or manifest["source_files"] != source_fingerprint():
            raise ValueError("Resume configuration, grid or source code changed; start a new run")
    else:
        run.mkdir(parents=True)
        manifest = dict(experiment=kind, smoke=args.smoke, signature=signature, source_files=source_fingerprint(),
                        total_ctx_per_arm=expected, repetitions=cfg.exp["repetitions"], sweep_space=space, tasks=tasks)
    workload, total_wei = pipeline.build_workload(cfg, run)
    if args.resume and manifest["workload_sha256"] != digest(workload):
        raise ValueError("Trace or selected workload changed")
    manifest["workload_sha256"] = digest(workload)
    write_json(run/"manifest.json", manifest)
    write_json(run/"params.json", to_params_dict(cfg))
    write_csv(run/"workload.csv", workload, list(workload[0]))
    try:
        for number, task in enumerate(manifest["tasks"], 1):
            if task["status"] == "complete":
                if load_completed(run, task, workload) is None:
                    raise ValueError("Resume task summary missing")
                continue
            print(f"[{number}/{len(tasks)}] {task['id']} / {task['token']}", flush=True)
            effective = task_cfg(cfg, task)
            effective = replace(effective, chain=replace(effective.chain, base_port=free_port_block(cfg.chain.base_port, cfg.chain.num_shards)))
            if kind == "exp025":
                parent = run/"tasks"/task["id"]/datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            else:
                # A distinct task summary prevents one failed arm from erasing completed paired data.
                parent = run/f"session_{task['round']}"/run.name
            parent.mkdir(parents=True, exist_ok=True)
            task["status"] = "running"
            write_json(run/"manifest.json", manifest)
            watchdog = RSSWatchdog(float(effective.exp["rss_cap_mb"]))
            watchdog.start()
            try:
                name, engine, fund, cap, eps, chi, hl, q_min, frac = pipeline._arm_spec(task["token"])
                stats = pipeline.run_arm(effective, task["tag"], workload,
                                         sum(r["amount_wei"] for r in workload), parent, watchdog,
                                         engine=engine, fund_eth=fund, cap_mult=cap, eps=eps, chi=chi, hl=hl, qmin=q_min, frac=frac)
                export_arm_data(effective, stats, parent/task["tag"], workload)
            finally:
                watchdog.stop()
            summary_path = parent/("summary.json" if kind == "exp025" else f"summary_{name}.json")
            write_json(summary_path, dict(task_id=task["id"], workload_sha256=digest(workload),
                                         params=to_params_dict(effective), arms=[stats], status={"complete": True}))
            task.update(status="complete", summary=str(summary_path.relative_to(run)))
            write_json(run/"manifest.json", manifest)
            assemble(run, manifest, cfg, workload)
    except BaseException as error:
        task.update(status="failed", error=str(error))
        write_json(run/"manifest.json", manifest)
        assemble(run, manifest, cfg, workload)
        raise
    assert assemble(run, manifest, cfg, workload)
    print(f"Completed verified data: {run}", flush=True)
    if not args.no_plot:
        plot_script = Path(cfg.exp["plotting_script"])
        if not plot_script.is_file():
            raise FileNotFoundError(f"Data saved; plotting script missing: {plot_script}")
        return subprocess.call([sys.executable, str(plot_script), "--experiment-source", str(run),
                                "--output", str(run/"figs"), "--heatmap-end-s",
                                str(cfg.exp.get("heatmap_end_s", 684))], cwd=ROOT)
    return 0
