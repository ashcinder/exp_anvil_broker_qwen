#!/usr/bin/env python3
"""exp008 · 定稿调度方案正式评估 —— 论文数据生产。

复用 exp003 的五方案同场管线（同一份代码，判据完全一致），连跑多场。
本脚本负责两件事：
  ① 驱动：每场以子进程运行 exp003/run.py，产物写进本包 out/<ts>/session_k/。
  ② 聚合：跨场合并每方案的 relay/搬运次数/搬量/段占比/gates，
     输出 report.json（中位/极值/场数 + 判据结论）。

为什么多场：单场 relay 有噪声（exp003 档案 §9/§16/§20 实测 2.5 倍散布）。
论文引用必须是跨场中位与区间，不是单次读数。

判据（写进 report.json verdicts）：
  V1 每场每方案完整性、预算、RPC 与 burn≡mint 校验全 True。
  V2 定稿方案相对同资金 plain 的 relay 中位数至少降低配置比例。
  V3 定稿方案相对 plain 的吞吐中位数损失不超过配置比例。

用法：
  python run.py                                   # 按 config.yaml 生产
  python run.py --set exp.exp008_sessions=1       # 临时改场数
  python run.py --set ...（其余 --set 原样转发给每场）
"""
import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"

sys.path.insert(0, str(ROOT))
from brokerlab.config import apply_overrides, load_config, to_params_dict  # noqa: E402
from brokerlab.reporting import arm_display_name  # noqa: E402


def prepare_valve_sweep(cfg):
    """Resolve YAML sweep settings into overrides shared by parent and exp003.

    Legacy configs remain unchanged unless the sweep is explicitly enabled.
    """
    enabled = cfg.exp.get("exp008_valve_sweep", False)
    if not isinstance(enabled, bool):
        raise ValueError("exp008_valve_sweep must be a YAML boolean")
    if not enabled:
        return cfg, []
    caps = cfg.exp.get("exp008_valve_thresholds")
    if not isinstance(caps, list) or not caps:
        raise ValueError("exp008_valve_thresholds must be a nonempty YAML list")
    caps = [float(v) for v in caps]
    if any(not math.isfinite(v) or v <= 1 for v in caps) or len(set(caps)) != len(caps):
        raise ValueError("Valve thresholds must be finite, unique and > 1")
    balance = float(cfg.exp["balances_eth"])
    count = float(cfg.exp["exp008_ctx_per_method"])
    sessions = float(cfg.exp["exp008_sessions"])
    brokers = cfg.scale.num_brokers
    if not math.isfinite(balance) or balance <= 0:
        raise ValueError("balances_eth must be finite and positive")
    if not math.isfinite(count) or count <= 0 or not count.is_integer() or brokers <= 0 or int(count) % brokers:
        raise ValueError("exp008_ctx_per_method must be a positive integer divisible by num_brokers")
    if not math.isfinite(sessions) or sessions <= 0 or not sessions.is_integer():
        raise ValueError("exp008_sessions must be a positive integer")
    arms = [f"plain@{balance}", *[f"valve@{balance}@{cap}" for cap in caps],
            f"tdr@{balance}@@0.1", f"topup@{balance}@@0.1",
            f"topup@{balance}@@0.95@@20"]
    note = ("Fixed-balance Valve threshold sweep with four shared controls. "
            "All arms use the same trace and within-session route seed. "
            "One session is exploratory, not evidence of statistical significance. "
            "Hard-window epsilon=0.1 versus EWMA epsilon=0.95: not a pure estimator ablation.")
    overrides = [f"exp.ctx_per_broker={int(count) // brokers}",
                 f"broker.initial_balance_eth={balance}",
                 "exp.arms=" + ",".join(arms),
                 f"exp.exp008_final_arm=topup@{balance}@0.95@20.0",
                 "exp.exp008_design_note=" + note]
    return apply_overrides(cfg, overrides), overrides


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"n": len(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals)}


def _expected_arm_tags(cfg):
    tags = []
    for token in (x.strip() for x in str(cfg.exp["arms"]).split(",")):
        if not token:
            continue
        parts = token.split("@")
        tag = parts[0]
        if len(parts) > 1 and parts[1]:
            tag += f"@{float(parts[1])}"
        for raw in parts[2:7]:
            if raw:
                tag += f"@{float(raw)}"
        if len(parts) > 7 and parts[7]:
            tag += f"@p{int(round(float(parts[7]) * 100))}"
        tags.append(tag)
    return tags


def aggregate(out_base: Path, session_dirs, cfg, session_runs=None) -> dict:
    """跨场聚合：按方案 tag 汇总，生成 report 结构。"""
    expected_sessions = int(float(cfg.exp.get("exp008_sessions", 2)))
    expected_tags = _expected_arm_tags(cfg)
    if session_runs is None:
        session_runs = [
            {"index": i, "path": str(path), "returncode": 0}
            for i, path in enumerate(session_dirs, 1)
        ]
    run_by_index = {int(r["index"]): r for r in session_runs}
    failed_sessions = sorted(
        i for i, r in run_by_index.items() if int(r.get("returncode", 1)) != 0
    )
    missing_summary_sessions = []
    missing_arms_by_session = {}
    unpassed_summary_sessions = []
    per = {}   # arm -> {relay:[], transfers:[], moved:[], legs:[], tp:[], gates:[...]}
    ctx_n = None
    loaded_sessions = []
    session_metrics = {}
    for index in range(1, expected_sessions + 1):
        run = run_by_index.get(index)
        sd = Path(run["path"]) if run and run.get("path") else None
        if sd is None:
            missing_summary_sessions.append(index)
            continue
        sfile = sd / "summary.json"
        if not sfile.exists():
            print(f"  !! 缺 summary：{sd}")
            missing_summary_sessions.append(index)
            continue
        d = json.loads(sfile.read_text())
        loaded_sessions.append(index)
        actual_tags = {a.get("arm") for a in d.get("arms", [])}
        missing = sorted(set(expected_tags) - actual_tags)
        if missing:
            missing_arms_by_session[str(index)] = missing
        if not d.get("passed", False):
            unpassed_summary_sessions.append(index)
        ctx_n = d["params"].get("_runtime", {}).get("total_ctx", ctx_n)
        for a in d.get("arms", []):
            session_metrics[(index, a["arm"])] = {
                "relay": a["relayed"],
                "throughput": a["throughput_ctx_per_s"],
            }
            e = per.setdefault(a["arm"], {"engine": a["engine"],
                                          "fund_eth": a["fund_eth"],
                                          "relay": [], "events": [],
                                          "transfers": [], "moved_wei": [],
                                          "legs_share": [], "throughput": [],
                                          "gates_all_ok": [], "burn_ok": []})
            t = a["tdr"]
            e["relay"].append(a["relayed"])
            e["events"].append(t["events_opened"])
            e["transfers"].append(t["transfers_done"])
            e["moved_wei"].append(t["moved_wei"])
            e["legs_share"].append(a["legs"]["tdr_share_of_ctx_legs"])
            e["throughput"].append(a["throughput_ctx_per_s"])
            e["gates_all_ok"].append(bool(a["gates"]) and all(a["gates"].values()))
            e["burn_ok"].append(a["burn_reconcile"]["ok"])
    arms = {}
    for tag, e in per.items():
        arms[tag] = {
            "engine": e["engine"], "fund_eth": e["fund_eth"],
            "relay": _stats(e["relay"]),
            "events": _stats(e["events"]),
            "transfers": _stats(e["transfers"]),
            "moved_eth": {k: round(v / 1e18, 1) for k, v in
                          (_stats(e["moved_wei"]) or
                           {"n": 0, "median": 0, "min": 0, "max": 0}).items()
                          if isinstance(v, (int, float)) and k != "n"},
            "legs_share": _stats(e["legs_share"]),
            "throughput_ctx_per_s": _stats(e["throughput"]),
            "sessions": len(e["relay"]),
            "gates_all_true": bool(e["gates_all_ok"]) and all(e["gates_all_ok"]),
            "burn_all_ok": bool(e["burn_ok"]) and all(e["burn_ok"]),
        }
    # —— 判据 ——
    complete = (not failed_sessions and not missing_summary_sessions
                and not missing_arms_by_session and not unpassed_summary_sessions
                and len(loaded_sessions) == expected_sessions
                and set(arms) == set(expected_tags)
                and all(a["sessions"] == expected_sessions for a in arms.values()))
    v1 = complete and bool(arms) and all(
        a["gates_all_true"] and a["burn_all_ok"] for a in arms.values())
    configured_final = str(cfg.exp.get("exp008_final_arm", "")).strip()
    final_tags = ([configured_final] if configured_final in arms else
                  [t for t in arms if "@0.95" in t and t.startswith("topup")])
    plain_tags = [t for t in arms if t == "plain" or t.startswith("plain@")]
    plain_tag = plain_tags[0] if len(plain_tags) == 1 else None
    min_reduction = float(cfg.exp.get("exp008_min_relay_reduction", 0.20))
    max_tp_loss = float(cfg.exp.get("exp008_max_throughput_loss", 0.15))
    relay_comparison = {}
    throughput_comparison = {}
    if plain_tag and arms[plain_tag]["relay"] and arms[plain_tag]["throughput_ctx_per_s"]:
        plain_relay = float(arms[plain_tag]["relay"]["median"])
        plain_tp = float(arms[plain_tag]["throughput_ctx_per_s"]["median"])
        for tag in final_tags:
            final_relay = float(arms[tag]["relay"]["median"])
            reduction = ((plain_relay - final_relay) / plain_relay
                         if plain_relay > 0 else None)
            paired_reductions = []
            paired_tp_losses = []
            for index in loaded_sessions:
                p = session_metrics.get((index, plain_tag))
                f = session_metrics.get((index, tag))
                if not p or not f:
                    continue
                if p["relay"] > 0:
                    paired_reductions.append(
                        (p["relay"] - f["relay"]) / p["relay"])
                if p["throughput"] > 0:
                    paired_tp_losses.append(
                        (p["throughput"] - f["throughput"]) / p["throughput"])
            relay_comparison[tag] = {
                "plain_median": plain_relay,
                "final_median": final_relay,
                "reduction": reduction,
                "paired_reduction": _stats(paired_reductions),
            }
            final_tp = float(arms[tag]["throughput_ctx_per_s"]["median"])
            loss = ((plain_tp - final_tp) / plain_tp if plain_tp > 0 else None)
            throughput_comparison[tag] = {
                "plain_median": plain_tp,
                "final_median": final_tp,
                "loss": loss,
                "paired_loss": _stats(paired_tp_losses),
            }
    # plain relay=0 时没有改善空间，不能据此宣称 TDR 有效。
    v2 = (complete and bool(final_tags) and bool(relay_comparison)
          and all(v["paired_reduction"] is not None
                  and v["paired_reduction"]["median"] >= min_reduction
                  for v in relay_comparison.values()))
    v3 = (complete and bool(final_tags) and bool(throughput_comparison)
          and all(v["paired_loss"] is not None
                  and v["paired_loss"]["median"] <= max_tp_loss
                  for v in throughput_comparison.values()))
    verdicts = {"V1_gates_every_session": v1,
                "V2_final_relay_reduction": {
                    "pass": v2, "minimum": min_reduction,
                    "plain_arm": plain_tag, "final_arms": final_tags,
                    "comparison": relay_comparison},
                "V3_final_throughput_loss": {
                    "pass": v3, "maximum": max_tp_loss,
                    "plain_arm": plain_tag, "final_arms": final_tags,
                    "comparison": throughput_comparison}}
    status = {
        "complete": complete,
        "expected_sessions": expected_sessions,
        "loaded_sessions": loaded_sessions,
        "failed_sessions": failed_sessions,
        "missing_summary_sessions": sorted(set(missing_summary_sessions)),
        "unpassed_summary_sessions": sorted(set(unpassed_summary_sessions)),
        "expected_arms": expected_tags,
        "missing_arms_by_session": missing_arms_by_session,
    }
    return {"params": to_params_dict(cfg), "ctx_total": ctx_n,
            "sessions": [str(s) for s in session_dirs],
            "session_runs": session_runs, "status": status,
            "arms": arms, "verdicts": verdicts,
            "verdict_all": v1 and v2 and v3}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true", help="Print resolved config without starting experiments or writing outputs")
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    cfg, generated = prepare_valve_sweep(cfg)
    effective_overrides = args.overrides + generated
    if args.dry_run:
        print(json.dumps(to_params_dict(cfg), ensure_ascii=False, indent=2))
        return 0
    sessions = int(float(cfg.exp.get("exp008_sessions", 2)))
    out_base = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_base.mkdir(parents=True)
    plan_names = [arm_display_name(tag, language="cn")
                  for tag in _expected_arm_tags(cfg)]
    print(f"  exp008 · 输出: {out_base} | 重复场次={sessions} | 方案={plan_names}")
    done_dirs = []
    session_runs = []
    for k in range(sessions):
        sess_root = out_base / f"session_{k + 1}"
        sess_root.mkdir(parents=True, exist_ok=True)
        print(f"\n  ==== 场次 {k + 1}/{sessions} → {sess_root} ====",
              flush=True)
        t0 = time.monotonic()
        # 不同场使用不同路由种子；同一场的全部 arm 仍共享该种子，保证公平。
        route_seed = int(cfg.exp.get("route_seed", 7)) + k * 10
        rc = subprocess.call(
            [sys.executable, str(EXP003_RUN), "--config", str(args.config),
             "--out-root", str(sess_root)] +
            [x for o in effective_overrides for x in ("--set", o)] +
            ["--set", f"exp.route_seed={route_seed}"])
        # exp003 在 sess_root 下再开时间戳目录；找到最新的一个
        subs = sorted([p for p in sess_root.iterdir() if p.is_dir()])
        result_dir = subs[-1] if subs else None
        if subs:
            done_dirs.append(result_dir)
        session_runs.append({"index": k + 1,
                             "path": str(result_dir) if result_dir else None,
                             "returncode": rc,
                             "route_seed": route_seed})
        print(f"  场次 {k + 1} 退出码={rc} 用时 {time.monotonic() - t0:.0f}s",
              flush=True)
        if rc != 0:
            print(f"  !! 场次 {k + 1} 判据未全过（退出码={rc}），继续跑完其余场")
    report = aggregate(out_base, done_dirs, cfg, session_runs=session_runs)
    (out_base / "report.json").write_text(json.dumps(report, indent=2,
                                                     ensure_ascii=False))
    print("\n" + "=" * 74)
    for tag, a in sorted(report["arms"].items()):
        r, m = a["relay"], a["moved_eth"]
        label = arm_display_name(tag, language="cn")
        checks = "通过" if a["gates_all_true"] and a["burn_all_ok"] else "失败"
        print(f"  {label}: Relay回退中位数 {r['median']} "
              f"（范围 {r['min']}～{r['max']}，样本 {r['n']} 场） | "
              f"再平衡金额中位数 {m['median']:,.0f} ETH | 完整性与守恒校验 {checks}")
    v = report["verdicts"]
    print(f"  V1 所有场次的完整性与守恒校验均通过: {v['V1_gates_every_session']}")
    print(f"  V2 定稿方案 Relay 中位数相对 plain 至少降低 "
          f"{v['V2_final_relay_reduction']['minimum']:.0%}: "
          f"{v['V2_final_relay_reduction']['pass']}")
    print(f"  V3 定稿方案吞吐中位数损失不超过 "
          f"{v['V3_final_throughput_loss']['maximum']:.0%}: "
          f"{v['V3_final_throughput_loss']['pass']}")
    print(f"  汇总报告: {out_base / 'report.json'}")
    print("=" * 74)
    return 0 if report["verdict_all"] else 1


if __name__ == "__main__":
    sys.exit(main())
