"""exp035 · 参数网格 runner（复用 exp025 的 pipeline，不动共享源码）。

与 exp025 的 runner 的差异只有三处，都是为了让「同一 policy 的多个参数点」
能在同一次运行里并存：

1. 任务来源不是 `exp.arms`，而是 config 里的 `exp.grid`（epsilon × q_min），
   并额外加一个 plain 基线臂；
2. 任务 id 与 summary 文件名带上参数（exp025 用 `name.split("@")[0]` 当 value，
   同一 policy 的多个参数点会撞成同一个 id / 同一个 summary_*.json）；
3. 收尾出图调用本目录的 analyze.py（按经济目标评分），而不是 paper 图脚本。

pipeline / paper_data / brokerlab 全部按原样 import，不改动、不 vendor，
所以已有的 exp025/033/034 结果的源码指纹不受影响。
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from experiments.exp025_regime_shift_sensitivity.paper_data import (
    export_arm_data, validate_arm, write_csv)
# 复用共享 runner 的纯工具函数（各自成文，无副作用）
from experiments.exp025_regime_shift_sensitivity.runner import (
    digest, free_port_block, token_tag, write_json)


def source_fingerprint():
    files = [Path(__file__),
             Path(pipeline.__file__),
             Path(__file__).parents[1] / "exp025_regime_shift_sensitivity" / "runner.py",
             Path(__file__).parents[1] / "exp025_regime_shift_sensitivity" / "paper_data.py"]
    files += sorted((ROOT / "brokerlab").glob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files}


def grid_points(cfg):
    """返回 [(label, token), ...]。两种模式：

    - `exp.grid.arms` 显式给出 [{label, token}, ...]（消融那种非网格的臂清单用）；
    - 否则 `plain`(可选) + `epsilon × q_min` 的 Top-up 网格。

    两种模式下都校验 label 与臂 tag 各自唯一：tag 会直接当输出目录名，
    撞了就会静默互相覆盖（exp025 的 runner 用 policy 名当任务 id 时踩过这个坑）。
    """
    grid = cfg.exp.get("grid") or {}
    explicit = grid.get("arms")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise ValueError("exp.grid.arms 需为非空列表")
        points = []
        for item in explicit:
            if not isinstance(item, dict) or "label" not in item or "token" not in item:
                raise ValueError("exp.grid.arms 每项需形如 {label: ..., token: ...}")
            points.append((str(item["label"]), str(item["token"])))
    else:
        eps_values = [float(v) for v in grid.get("epsilon", [])]
        q_values = [float(v) for v in grid.get("q_min", [])]
        hl = float(grid.get("half_life", 20.0))
        if not eps_values or not q_values:
            raise ValueError("exp.grid.epsilon / exp.grid.q_min 不能为空")
        for e in eps_values:
            if not 0.0 < e < 1.0:
                raise ValueError(f"epsilon 需在 (0,1) 内（单侧触发）：{e}")
        if any(q < 0.0 for q in q_values):
            raise ValueError("q_min 不能为负")
        fund = cfg.exp["balances_eth"]
        points = []
        if grid.get("include_plain", True):
            points.append(("plain", f"plain@{fund}"))
        for eps in eps_values:
            for q in q_values:
                points.append((f"eps{eps:g}_qmin{q:g}",
                               f"topup@{fund}@@{eps:g}@@{hl:g}@{q:g}"))

    labels = [label for label, _ in points]
    if len(set(labels)) != len(labels):
        raise ValueError(f"网格点标签重复: {labels}")
    tags = [token_tag(token) for _, token in points]
    if len(set(tags)) != len(tags):
        raise ValueError(f"臂 tag 重复（输出目录会互相覆盖）: {tags}")
    return points


def tasks_for(cfg):
    points = grid_points(cfg)
    rounds = int(cfg.exp["repetitions"])
    tasks = []
    for rep in range(1, rounds + 1):
        seed = int(cfg.exp["route_seed"]) + (rep - 1) * int(cfg.exp["route_seed_step"])
        for label, token in points:
            tasks.append(dict(id=f"{label}_r{rep}", sweep="econ_grid", value=label,
                              round=rep, route_seed=seed, token=token,
                              tag=token_tag(token), status="pending"))
    return tasks


def task_cfg(cfg, task):
    return apply_overrides(cfg, [f"exp.route_seed={task['route_seed']}",
                                 f"exp.arms={task['token']}"])


def load_completed(run, task, workload, validate=True, workload_hash=None):
    summary_path = run / task.get("summary", "tasks/_missing/summary.json")
    if not summary_path.is_file():
        return None
    doc = json.loads(summary_path.read_text(encoding="utf-8"))
    if doc.get("task_id") != task["id"] or doc.get("workload_sha256") != (
            workload_hash or digest(workload)):
        raise ValueError(f"Saved task identity/workload mismatch: {task['id']}")
    stats = doc["arms"][0]
    if stats["arm"] != task["tag"] or doc["params"]["exp"]["route_seed"] != task["route_seed"]:
        raise ValueError("Saved arm/route seed mismatch")
    if validate:
        validate_arm(stats, summary_path.parent / task["tag"], workload)
    return stats


def assemble(run, manifest, cfg, workload):
    records = []
    workload_hash = digest(workload)
    for task in manifest["tasks"]:
        if task["status"] != "complete":
            continue
        stats = load_completed(run, task, workload, validate=False,
                               workload_hash=workload_hash)
        if stats is None:
            raise ValueError("Completed task has no summary")
        records.append(dict(task_id=task["id"], sweep=task["sweep"], value=task["value"],
                            round=task["round"], route_seed=task["route_seed"],
                            n=stats["n"], served=stats["served"], relayed=stats["relayed"],
                            service_ratio=stats["served"] / stats["n"],
                            transfers=stats["tdr"]["transfers_done"],
                            events=stats["tdr"]["events_opened"],
                            moved_eth=stats["tdr"]["moved_wei"] / 10 ** 18,
                            throughput=stats["throughput_ctx_per_s"],
                            gates_ok=True, burn_ok=True, summary=task["summary"]))
    if records:
        write_csv(run / "raw_results.csv", records, list(records[0]))
    complete = len(records) == len(manifest["tasks"])
    report = dict(experiment=manifest["experiment"],
                  formal=bool(manifest.get("formal", not manifest.get("smoke"))),
                  params=to_params_dict(cfg),
                  status={"complete": complete,
                          "expected_arms": list(dict.fromkeys(t["tag"] for t in manifest["tasks"]))},
                  total_ctx_per_arm=len(workload), repetitions=cfg.exp["repetitions"],
                  workload_sha256=workload_hash, completed_tasks=len(records),
                  planned_tasks=len(manifest["tasks"]), raw_results=records,
                  accounting=("Artificial destination rotation on selected ETH_cleaned trace; "
                              "control-message bytes + signed Relay burn and receipt bytes. "
                              "Profit cost is an explicit nominal model; chain gasPrice=0."))
    write_json(run / "report.json", report)
    return complete


def main(here, kind, default_config=None, extra_argv=None):
    parser = argparse.ArgumentParser(description=f"{kind}: 经济目标下的 TDR 参数重调")
    parser.add_argument("--config", type=Path, default=default_config or here / "config.yaml")
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--run-dir", type=Path,
                        help="指定确切的产物目录（不存在则新建；已有匹配 manifest 则续跑）")
    parser.add_argument("--output-root", type=Path, default=here / "out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke", action="store_true",
                        help="80 CTX, 1 broker, 1 round；产物标注 smoke")
    parser.add_argument("--rounds", type=int, default=None,
                        help="分析档：显式覆盖场次数（少于正式档即标注为非正式）")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(extra_argv)

    cfg = apply_overrides(load_config(args.config), args.set)
    reduced = args.rounds is not None
    if reduced:
        cfg = apply_overrides(cfg, [f"exp.repetitions={args.rounds}"])
    if args.smoke:
        cfg = replace(cfg, scale=replace(cfg.scale, num_brokers=1),
                      broker=replace(cfg.broker, initial_balance_eth=5))
        cfg = apply_overrides(cfg, ["exp.ctx_per_broker=80", "exp.repetitions=1",
                                    "exp.rate=5", "exp.balances_eth=5",
                                    "exp.drain_tail_s=240"])
    expected = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    required_ctx = pipeline.exp_param(cfg, "formal_ctx_per_arm", int)
    required_rounds = pipeline.exp_param(cfg, "formal_rounds", int)
    if not args.smoke and not reduced and (expected != required_ctx
                                           or int(cfg.exp["repetitions"]) != required_rounds):
        raise ValueError(f"正式 {kind} 需要每臂 {required_ctx} CTX、{required_rounds} 场；"
                         "先用 --smoke 或 --rounds 做检查")
    if reduced and expected != required_ctx:
        raise ValueError(f"分析档仍需每臂 {required_ctx} CTX")

    tasks = tasks_for(cfg)
    signature = digest(dict(params=to_params_dict(cfg), kind=kind, smoke=args.smoke))
    print(f"{kind}: {len(tasks)} arm runs x {expected} CTX = {len(tasks)*expected:,}; "
          f"serial; smoke={args.smoke}", flush=True)
    print("  网格: " + ", ".join(t["value"] for t in tasks if t["round"] == 1), flush=True)
    if args.dry_run:
        print(json.dumps(dict(config=str(args.config), planned_tasks=len(tasks),
                              rounds=cfg.exp["repetitions"], ctx_per_arm=expected,
                              grid=tasks[:3]), indent=2, ensure_ascii=False))
        return 0

    run = (args.run_dir.resolve() if args.run_dir
           else args.resume.resolve() if args.resume
           else args.output_root.resolve() / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    resuming = bool(args.resume) or (bool(args.run_dir) and (run / "manifest.json").is_file())
    if resuming:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if manifest["signature"] != signature or manifest["source_files"] != source_fingerprint():
            raise ValueError("续跑的配置、网格或源码已变；请新建运行目录")
    else:
        run.mkdir(parents=True, exist_ok=True)
        manifest = dict(experiment=kind, smoke=args.smoke, analysis_pass=bool(reduced),
                        formal=not (args.smoke or reduced), signature=signature,
                        source_files=source_fingerprint(), total_ctx_per_arm=expected,
                        repetitions=cfg.exp["repetitions"], grid=cfg.exp.get("grid"),
                        tasks=tasks)
    workload, total_wei = pipeline.build_workload(cfg, run)
    if resuming and manifest.get("workload_sha256") != digest(workload):
        raise ValueError("trace 或选出的负载已变")
    manifest["workload_sha256"] = digest(workload)
    write_json(run / "manifest.json", manifest)
    write_json(run / "params.json", to_params_dict(cfg))
    write_csv(run / "workload.csv", workload, list(workload[0]))
    try:
        for number, task in enumerate(manifest["tasks"], 1):
            if task["status"] == "complete":
                if load_completed(run, task, workload) is None:
                    raise ValueError("续跑任务缺少 summary")
                continue
            print(f"[{number}/{len(tasks)}] {task['id']} / {task['token']}", flush=True)
            effective = task_cfg(cfg, task)
            effective = replace(effective, chain=replace(
                effective.chain,
                base_port=free_port_block(cfg.chain.base_port, cfg.chain.num_shards)))
            parent = run / f"session_{task['round']}" / run.name
            parent.mkdir(parents=True, exist_ok=True)
            task["status"] = "running"
            write_json(run / "manifest.json", manifest)
            watchdog = RSSWatchdog(float(effective.exp["rss_cap_mb"]))
            watchdog.start()
            try:
                name, engine, fund, cap, eps, chi, hl, q_min, frac = pipeline._arm_spec(task["token"])
                stats = pipeline.run_arm(effective, task["tag"], workload, total_wei, parent,
                                         watchdog, engine=engine, fund_eth=fund, cap_mult=cap,
                                         eps=eps, chi=chi, hl=hl, qmin=q_min, frac=frac)
                export_arm_data(effective, stats, parent / task["tag"], workload)
            finally:
                watchdog.stop()
            # summary 文件名带参数，否则同一 policy 的多个网格点会互相覆盖
            summary_path = parent / f"summary_{task['tag']}.json"
            write_json(summary_path, dict(task_id=task["id"],
                                          workload_sha256=digest(workload),
                                          params=to_params_dict(effective), arms=[stats],
                                          status={"complete": True}))
            task.update(status="complete", summary=str(summary_path.relative_to(run)))
            write_json(run / "manifest.json", manifest)
            assemble(run, manifest, cfg, workload)
    except BaseException as error:
        task.update(status="failed", error=str(error))
        write_json(run / "manifest.json", manifest)
        assemble(run, manifest, cfg, workload)
        raise
    assert assemble(run, manifest, cfg, workload)
    print(f"Completed verified data: {run}", flush=True)
    if not args.no_plot:
        return subprocess.call([sys.executable, str(here / "analyze.py"),
                                "--source", str(run), "--output", str(run / "figs")], cwd=ROOT)
    return 0
