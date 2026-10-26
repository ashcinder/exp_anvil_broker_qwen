"""Serial, resumable experiments with verified raw outputs and plot compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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
from protocol import register, validate_protocol, PROTOCOL
register(pipeline)
from experiments.exp025_regime_shift_sensitivity.paper_data import export_arm_data, read_csv, validate_arm, write_csv


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint():
    files = [Path(pipeline.__file__), Path(export_arm_data.__code__.co_filename)]
    files += sorted((ROOT / 'brokerlab').glob('*.py'))
    files += sorted(Path(__file__).parent.glob('*.py'))
    files += [ROOT / 'experiments/exp033_sota_comparison/plot_pareto.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


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
    if rounds <= 0:
        raise ValueError("repetitions must be positive")
    if kind in ("exp025", "exp034"):
        if not space or set(space) - ({"q_min", "half_life", "epsilon", "low_water_ratio"}
                                      if kind == "exp034" else {"q_min", "half_life", "epsilon"}):
            raise ValueError("Sensitivity space must contain supported dimensions")
        for parameter, spec in space.items():
            values = spec["values"]
            if not values or len(set(values)) != len(values):
                raise ValueError(f"Empty or duplicate grid values: {parameter}")
    for rep in range(1, rounds + 1):
        seed = int(cfg.exp["route_seed"]) + (rep - 1) * int(cfg.exp["route_seed_step"])
        settings = []
        if kind in ("exp025", "exp034"):
            for parameter, spec in space.items():
                for value in spec["values"]:
                    state = dict(q_min=0.3, half_life=20, epsilon=0.95)
                    if kind == "exp034":
                        state["low_water_ratio"] = float(cfg.exp["low_water_ratio"])
                    state.update(spec["fixed"])
                    state[parameter] = value
                    epsilon_ok = (math.isfinite(state["epsilon"]) and state["epsilon"] >= 0
                                  and (kind == "exp034" or state["epsilon"] <= 1))
                    if not 0 <= state["q_min"] <= 1 or not epsilon_ok or not math.isfinite(state["half_life"]) or state["half_life"] <= 0:
                        raise ValueError(f"Invalid sensitivity setting: {state}")
                    if kind == "exp034" and (not math.isfinite(state["low_water_ratio"])
                            or not 0 <= state["low_water_ratio"] <= 1):
                        raise ValueError(f"Invalid low-water ratio: {state}")
                    fund = cfg.exp["balances_eth"]
                    policy = "topup_lowwater" if kind == "exp034" else "topup"
                    token = f"{policy}@{fund}@@{state['epsilon']}@@{state['half_life']}@{state['q_min']}"
                    settings.append((parameter, value, token, state.get("low_water_ratio")))
        else:
            settings = [("figures", name.split("@")[0], name, None)
                        for name in cfg.exp["arms"].split(",")]
        for parameter, value, token, lower in settings:
            slug = f"{parameter}_{value}_r{rep}"
            tasks.append(dict(id=slug, sweep=parameter, value=value, round=rep,
                              route_seed=seed, token=token, tag=token_tag(token), status="pending"))
            if lower is not None:
                # Keep Windows artifact paths short; the full token and alpha remain in manifest.
                short_name = {"q_min": "q", "half_life": "h", "epsilon": "e", "low_water_ratio": "a"}[parameter]
                tasks[-1].update(low_water_ratio=lower, tag=f"lw_{short_name}{value}@a{lower}")
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
    overrides = [f"exp.route_seed={task['route_seed']}", f"exp.arms={task['token']}"]
    if "low_water_ratio" in task:
        _, _, _, _, eps, _, half_life, q_min, _ = pipeline._arm_spec(task['token'])
        overrides += [f"exp.low_water_ratio={task['low_water_ratio']}",
                      f"exp.tdr_epsilon={eps}", f"exp.tdr_q_min={q_min}",
                      f"exp.tdr_ewma_half_life={half_life}"]
    return apply_overrides(cfg, overrides)


def validate_task_params(task, doc):
    """Reject mismatched execution parameters, including the independent lower threshold."""
    exp = doc['params']['exp']
    if exp['route_seed'] != task['route_seed']:
        raise ValueError('Saved route seed mismatch')
    if 'low_water_ratio' in task:
        _, engine, _, _, eps, _, half_life, q_min, _ = pipeline._arm_spec(task['token'])
        expected = dict(arms=task['token'], low_water_ratio=task['low_water_ratio'],
                        tdr_epsilon=eps, tdr_q_min=q_min, tdr_ewma_half_life=half_life)
        if any(exp.get(key) != value for key, value in expected.items()):
            raise ValueError('Saved task execution parameters mismatch')
        if doc['arms'][0].get('engine') != engine:
            raise ValueError('Saved task engine mismatch')


def task_directory(run, number):
    """Compact attempt directories avoid Windows MAX_PATH at the default checkout."""
    return Path(run)/"t"/f"{number:04d}"/datetime.now().strftime("%H%M%S%f")


def load_completed(run, task, workload, validate=True, workload_hash=None):
    summary_path = run / task.get("summary", "tasks/_missing/summary.json")
    if not summary_path.is_file():
        return None
    doc = json.loads(summary_path.read_text(encoding="utf-8"))
    if doc.get("task_id") != task["id"] or doc.get("workload_sha256") != (workload_hash or digest(workload)):
        raise ValueError(f"Saved task identity/workload mismatch: {task['id']}")
    validate_task_params(task, doc)
    stats = doc["arms"][0]
    if (not doc.get("status", {}).get("complete") or not stats.get("gates")
            or not all(stats["gates"].values()) or not stats["burn_reconcile"]["ok"]
            or stats["n"] != len(workload) or stats.get("failed", 0)
            or stats["served"] + stats["relayed"] != stats["n"]
            or stats["tdr"].get("transfers_lost", 0) or stats["tdr"].get("lost_in_transit_wei", 0)):
        raise ValueError("Saved task incomplete or failed accounting gates")
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
    report = dict(experiment=manifest["experiment"],
                  formal=bool(manifest.get("formal", not manifest.get("smoke"))),
                  params=to_params_dict(cfg),
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
    parser.add_argument("--only", help="Sensitivity dimensions: q_min, half_life, epsilon, low_water_ratio (exp034), comma separated")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--output-root", type=Path, default=here/"out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="80 CTX, 1 broker, 1 round; outputs labeled smoke")
    parser.add_argument("--rounds", type=int, default=None,
                        help="分析档：显式覆盖场次数（少于正式档即标注为非正式，供先看效果用）")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), args.set)
    # 分析档（--rounds）：允许先用少于正式档的场次看效果；正式跑不得使用，
    # 且产物在 report.json/manifest.json 里显式标记为非正式，防止误引用。
    reduced = args.rounds is not None and kind != "exp025"
    if reduced:
        cfg = apply_overrides(cfg, [f"exp.repetitions={args.rounds}"])
    space = yaml.safe_load(args.space.read_text(encoding="utf-8")) if kind in ("exp025", "exp034") else None
    if args.only:
        if kind not in ("exp025", "exp034"):
            raise ValueError("--only applies to sensitivity dimensions")
        names = args.only.split(",")
        if set(names) - set(space):
            raise ValueError(f"Unknown dimension: {names}")
        space = {name: space[name] for name in names}
    if args.smoke:
        cfg = replace(cfg, scale=replace(cfg.scale, num_brokers=1), broker=replace(cfg.broker, initial_balance_eth=45))
        cfg = apply_overrides(cfg, ["exp.ctx_per_broker=80", "exp.repetitions=1", "exp.rate=5",
                                   "exp.balances_eth=5", "exp.drain_tail_s=240"])
        if space:
            space = {key: {**spec, "values": spec["values"][:1]} for key, spec in space.items()}
        if kind in ("exp043", "exp034"):
            # Keep the SOTA reference capital: 1 broker sees aggregate traffic;
            # reducing its capital to 5 ETH can make offline PnP infeasible.
            cfg = apply_overrides(cfg, ["exp.balances_eth=45", "exp.sota_pnps_epoch_blocks=1"])
            arms = []
            for token in cfg.exp["arms"].split(","):
                parts = token.split("@")
                if len(parts) > 1:
                    parts[1] = "45"
                arms.append("@".join(parts))
            cfg = apply_overrides(cfg, ["exp.arms=" + ",".join(arms)])
        if kind == "exp026":
            cfg = apply_overrides(cfg, ["exp.arms=plain@5,topup@5@@0.95@@20@0.3", "exp.profit_broker_idx=0"])
    validate_protocol(cfg, smoke=args.smoke, analysis=reduced)
    expected = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    # 每个实验的正式档规模（CTX/臂, 场次）。exp033 与 exp025 同规模同路由种子，
    # 这样 SOTA 对照点与已发表的 TDR/plain 点落在同一条负载上，可直接比。
    requirements = {"exp025": (40000, 10), "exp026": (80000, 1), "exp043": (40000, 3), "exp034": (40000, 10)}
    if kind not in requirements:
        raise ValueError(f"Unknown experiment kind {kind!r}")
    required_ctx, required_rounds = requirements[kind]
    if not args.smoke and not reduced and (expected != required_ctx
                                           or int(cfg.exp["repetitions"]) != required_rounds):
        raise ValueError(f"Formal {kind} requires {required_ctx} CTX per arm and {required_rounds} rounds; use --smoke for checks")
    if reduced and expected != required_ctx:
        raise ValueError(f"Analysis pass still needs {required_ctx} CTX per arm")
    tasks = tasks_for(cfg, kind, space)
    if len({t["id"] for t in tasks}) != len(tasks):
        raise ValueError("Duplicate arm names/task IDs; use named ablations")
    if kind in ("exp043", "exp034"):
        from brokerlab.sota_native import REVISION
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
        manifest = dict(experiment=kind, smoke=args.smoke,
                        analysis_pass=bool(reduced), formal=not (args.smoke or reduced),
                        signature=signature, source_files=source_fingerprint(),
                        total_ctx_per_arm=expected, repetitions=cfg.exp["repetitions"], sweep_space=space, tasks=tasks)
    workload, total_wei = pipeline.build_workload(cfg, run)
    if args.resume and manifest["workload_sha256"] != digest(workload):
        raise ValueError("Trace or selected workload changed")
    manifest["workload_sha256"] = digest(workload)
    manifest["protocol_revision"] = PROTOCOL
    if kind in ("exp043", "exp034"):
        manifest["algorithm_revision"] = ("exp034-independent-lowwater-v3" if kind == "exp034" else REVISION)
        if int(cfg.exp.get("sota_training_ctx", 0)):
            manifest["training_sha256"] = hashlib.sha256((run / "training.json").read_bytes()).hexdigest()
        manifest["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        manifest["git_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    write_json(run/"manifest.json", manifest)
    write_json(run/"params.json", to_params_dict(cfg))
    write_csv(run/"workload.csv", workload, list(workload[0]))
    try:
        for number, task in enumerate(manifest["tasks"], 1):
            if manifest["source_files"] != source_fingerprint():
                raise ValueError("Source changed during run; start a new versioned run")
            if task["status"] == "complete":
                if load_completed(run, task, workload) is None:
                    raise ValueError("Resume task summary missing")
                continue
            print(f"[{number}/{len(tasks)}] {task['id']} / {task['token']}", flush=True)
            effective = task_cfg(cfg, task)
            effective = replace(effective, chain=replace(effective.chain, base_port=free_port_block(cfg.chain.base_port, cfg.chain.num_shards)))
            if kind == "exp034":
                parent = task_directory(run, number)
            elif kind == "exp025":
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
                if manifest["source_files"] != source_fingerprint():
                    raise ValueError("Source changed while task was running")
            finally:
                watchdog.stop()
            summary_path = parent/("summary.json" if kind in ("exp025", "exp034") else f"summary_{name}.json")
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
        plot_args=[sys.executable, str(plot_script), "--experiment-source", str(run),
                   "--output", str(run/"figs"), "--heatmap-end-s", str(cfg.exp.get("heatmap_end_s", 684))]
        if not manifest["formal"]: plot_args.append("--allow-analysis")
        return subprocess.call(plot_args,cwd=ROOT)
    return 0
