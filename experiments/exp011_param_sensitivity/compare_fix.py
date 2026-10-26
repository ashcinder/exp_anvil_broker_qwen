#!/usr/bin/env python3
"""exp011 · 估计表刷新回归修复 / 加速 的对照实验。

背景：coordinator 的"每块全量刷新余额估计表"自 exp003 起是死代码（回归），
导致 TDR 代铸补进来的 dst 余额对路由器永远不可见，实测 72% 的 CTX 退回随机指派。
已按 exp007 的原判据修复（见 exp003/run.py 的注释）。

本脚本用 **exp008 有历史数据的那组参数**（16 分片 × 150 垫资 × N=40000 × rate120，
5 方案同场）跑两次，回答两个问题：
  ① 修复前(A0=exp008 历史) vs 修复后(A1)：结论差多少？
  ② 修复后(A1) vs 修复后+加速(B)：加速还改不改结论？

用法：
  python compare_fix.py                     # 两轮都跑（约 80 min）
  python compare_fix.py --only A            # 只跑修复后基线
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"
# 用 exp008 的正式档当基底（它有历史数据可比）
BASE_CFG = HERE.parent / "exp008_tdr_schedule_final" / "config.yaml"
REF_REPORT = (HERE.parent / "exp008_tdr_schedule_final" / "out"
              / "20260910_033927" / "report.json")
ARMS = "plain@150,valve@150@1.3,tdr@150@@0.1,topup@150@@0.1,topup@150@@0.95@@20"

# 修复后的刷新节奏：每块刷（cadence=1）在 16 分片下要 800 次 RPC/次、协调器 92%
# 时间耗在刷新上（实测吞吐 67→42.3）。cadence=5 + 16 线程实测恢复到 63.9 CTX/s，
# 且 no_qualified_est 仍为 0。两组都用同一节奏，保证差异只来自"加速旋钮"。
# 修复的代价必须显式记账：协调器每块要多花时间做全量刷新，dispatch 从 ~76/块
# 掉到 ~58/块（实测第一次对照因此撞穿 exp008 的 max_backlog=20000 而 abort）。
# 故：①刷新放宽到每 10 块一次；②backlog 上限按新水位放大到 40000。
# 这两项是"修复的代价"本身，不是可选项——对比时要一起报。
FIXED = ["exp.refresh_every_blocks=10", "exp.refresh_workers=16",
         "exp.max_backlog=40000"]
RUNS = {
    # A1 = 修复后基线：沿用 exp008 参数（除上面两项被修复代价逼着放大）
    "A1_fixed_baseline": list(FIXED),
    # B = 修复后 + 加速：window 2→8（解开并发手刹）+ rate 120→400（提前暴露 key）
    "B_fixed_speedup": list(FIXED) + ["exp.sender_window=8", "exp.rate=400",
                                      "exp.drain_tail_s=600"],
}
ETH = 10 ** 18


def run_one(label, sets, out_base):
    sess = out_base / label
    sess.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(BASE_CFG),
           "--out-root", str(sess), "--set", f"exp.arms={ARMS}"]
    for x in sets:
        cmd += ["--set", x]
    print(f"\n  ---- {label} ----", flush=True)
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dur = time.monotonic() - t0
    (sess / "driver.log").write_text(p.stdout + p.stderr)
    subs = sorted([d for d in sess.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime)
    sfile = (subs[-1] / "summary.json") if subs else None
    if not sfile or not sfile.exists():
        print(f"  {label}: !! 无 summary (rc={p.returncode})", flush=True)
        return None
    print(f"  {label}: {dur/60:.1f} min", flush=True)
    return sfile


def load(sfile):
    d = json.loads(Path(sfile).read_text())
    out = {}
    for a in d["arms"]:
        co = {}
        cf = Path(sfile).parent / a["arm"] / "coordinator.json"
        if cf.exists():
            co = json.loads(cf.read_text())
        out[a["arm"]] = {
            "relay": a["relayed"],
            "moved": a["tdr"]["moved_wei"] / ETH,
            "transfers": a["tdr"]["transfers_done"],
            "events": a["tdr"]["events_opened"],
            "thr": a["throughput_ctx_per_s"],
            "gates": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
            "nq_est": co.get("no_qualified_est"),
            "refresh_ms": co.get("refresh_ms_mean"),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="逗号分隔的 run 名白名单")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    out_base = HERE / "out" / f"fixcmp_{datetime.now():%Y%m%d_%H%M%S}"
    out_base.mkdir(parents=True)
    print(f"  exp011 修复对照 · out: {out_base}")
    print(f"  基底 = exp008 参数（16 分片 × 150 垫资 × N=40000 × rate120）")
    print(f"  参照 = exp008 历史 {REF_REPORT.parent.name}（修复前）")

    got = {}
    for label, sets in RUNS.items():
        if only and label.split("_")[0] not in only:
            continue
        sf = run_one(label, sets, out_base)
        if sf:
            got[label] = load(sf)

    # 参照组
    ref = {}
    if REF_REPORT.exists():
        r = json.loads(REF_REPORT.read_text())
        for tag, a in r["arms"].items():
            ref[tag] = {"relay": a["relay"]["median"],
                        "moved": a["moved_eth"]["median"], "thr": None,
                        "gates": a["gates_all_true"] and a["burn_all_ok"]}
    got["A0_exp008_history"] = ref

    # —— 汇总表 ——
    arms = [a for a in got.get("A1_fixed_baseline", {})] or list(ref)
    order = ["A0_exp008_history", "A1_fixed_baseline", "B_fixed_speedup"]
    order = [k for k in order if k in got]
    print("\n" + "=" * 100)
    print(f"  {'方案':<24} {'组':<22} {'relay':>8} {'搬量 ETH':>12} {'CTX/s':>8} "
          f"{'est失配':>9} {'refresh_ms':>11} {'gates':>6}")
    for tag in arms:
        for k in order:
            r = got[k].get(tag)
            if not r:
                continue
            def f(v, fmt="{:,}"):
                return fmt.format(v) if isinstance(v, (int, float)) else "-"
            print(f"  {tag:<24} {k:<22} {f(r['relay']):>8} {f(r['moved'],'{:,.0f}'):>12} "
                  f"{f(r.get('thr'),'{:.1f}'):>8} {f(r.get('nq_est')):>9} "
                  f"{f(r.get('refresh_ms'),'{:.1f}'):>11} "
                  f"{'OK' if r['gates'] else 'FAIL':>6}")
        print("  " + "-" * 96)
    (out_base / "compare.json").write_text(json.dumps(got, indent=2,
                                                      ensure_ascii=False))
    print(f"  compare: {out_base / 'compare.json'}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
