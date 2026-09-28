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
  V1 每场每方案 gates（G1-G4 + burn≡mint）全 True。
  V2 定稿方案（含 @0.95 的 topup）relay 跨场中位 ≤ exp008_relay_median_max。
  V3 定稿方案搬量跨场中位落在物理下限 ±10%（档案 §19，下限 ≈164k ETH，
     按 ctx 数线性折算；ctx 数从 params.exp.ctx_per_broker 推）。

用法：
  python run.py                                   # 按 config.yaml 生产
  python run.py --set exp.exp008_sessions=1       # 临时改场数
  python run.py --set ...（其余 --set 原样转发给每场）
"""
import argparse
import json
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


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"n": len(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals)}


def aggregate(out_base: Path, session_dirs, cfg) -> dict:
    """跨场聚合：按方案 tag 汇总，生成 report 结构。"""
    per = {}   # arm -> {relay:[], transfers:[], moved:[], legs:[], tp:[], gates:[...]}
    ctx_n = None
    for sd in session_dirs:
        sfile = sd / "summary.json"
        if not sfile.exists():
            print(f"  !! 缺 summary：{sd}")
            continue
        d = json.loads(sfile.read_text())
        ctx_n = d["params"].get("_runtime", {}).get("total_ctx", ctx_n)
        for a in d["arms"]:
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
            e["gates_all_ok"].append(all(a["gates"].values()))
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
            "gates_all_true": all(e["gates_all_ok"]),
            "burn_all_ok": all(e["burn_ok"]),
        }
    # —— 判据 ——
    v1 = all(a["gates_all_true"] and a["burn_all_ok"] for a in arms.values()) \
        and len(arms) > 0
    final_tags = [t for t in arms if "@0.95" in t and t.startswith("topup")]
    cap = float(cfg.exp.get("exp008_relay_median_max", 50))
    v2 = bool(final_tags) and all(arms[t]["relay"]["median"] <= cap
                                  for t in final_tags)
    v3 = True
    if ctx_n and final_tags:
        # 物理下限 ≈ 建仓 82k + 净排水 82k @40000 → 164k；按 ctx 线性折算 ±10%
        floor_eth = 164000.0 * ctx_n / 40000
        for t in final_tags:
            med = arms[t]["moved_eth"]["median"]
            v3 = v3 and (0.9 * floor_eth <= med <= 1.1 * floor_eth)
    verdicts = {"V1_gates_every_session": v1,
                "V2_final_relay_median_le_cap": {"pass": v2, "cap": cap,
                                                 "final_arms": final_tags},
                "V3_final_moved_near_physical_floor": v3}
    return {"params": to_params_dict(cfg), "ctx_total": ctx_n,
            "sessions": [str(s) for s in session_dirs],
            "arms": arms, "verdicts": verdicts,
            "verdict_all": v1 and v2 and v3}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    sessions = int(float(cfg.exp.get("exp008_sessions", 2)))
    out_base = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_base.mkdir(parents=True)
    print(f"  exp008 · out: {out_base} | sessions={sessions} "
          f"| arms={cfg.exp['arms']}")
    done_dirs = []
    for k in range(sessions):
        sess_root = out_base / f"session_{k + 1}"
        sess_root.mkdir(parents=True, exist_ok=True)
        print(f"\n  ==== session {k + 1}/{sessions} → {sess_root} ====",
              flush=True)
        t0 = time.monotonic()
        rc = subprocess.call(
            [sys.executable, str(EXP003_RUN), "--config", str(args.config),
             "--out-root", str(sess_root)] +
            [x for o in args.overrides for x in ("--set", o)])
        # exp003 在 sess_root 下再开时间戳目录；找到最新的一个
        subs = sorted([p for p in sess_root.iterdir() if p.is_dir()])
        if subs:
            done_dirs.append(subs[-1])
        print(f"  session {k + 1} rc={rc} 用时 {time.monotonic() - t0:.0f}s",
              flush=True)
        if rc != 0:
            print(f"  !! session {k + 1} 判据未全过（rc={rc}），继续跑完其余场")
    report = aggregate(out_base, done_dirs, cfg)
    (out_base / "report.json").write_text(json.dumps(report, indent=2,
                                                     ensure_ascii=False))
    print("\n" + "=" * 74)
    for tag, a in sorted(report["arms"].items()):
        r, m = a["relay"], a["moved_eth"]
        print(f"  {tag:<26} relay 中位 {r['median']:>6} "
              f"[{r['min']}..{r['max']}] n={r['n']} | "
              f"搬量中位 {m['median']:>10,.0f} | gates "
              f"{'OK' if a['gates_all_true'] and a['burn_all_ok'] else 'FAIL'}")
    v = report["verdicts"]
    print(f"  V1 gates 每场全过: {v['V1_gates_every_session']}")
    print(f"  V2 定稿 relay 中位 ≤ {v['V2_final_relay_median_le_cap']['cap']}: "
          f"{v['V2_final_relay_median_le_cap']['pass']}")
    print(f"  V3 定稿搬量≈物理下限±10%: {v['V3_final_moved_near_physical_floor']}")
    print(f"  report: {out_base / 'report.json'}")
    print("=" * 74)
    return 0 if report["verdict_all"] else 1


if __name__ == "__main__":
    sys.exit(main())
