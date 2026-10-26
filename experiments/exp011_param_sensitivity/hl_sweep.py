#!/usr/bin/env python3
"""exp011 · 半衰期（hl）参数搜索 —— 修复版代码，4 万笔/轮，每点 5 轮。

用户 2026-09-29 指定：
  · hl ∈ {1,2,3,4,5,6,8,10,12,15,18,20,25,30,35,40,45,50}（18 点）
  · 每点 5 轮（每轮一条独立链），每轮总量 40000 笔
  · 其余参数沿用 exp008 定稿：ε=0.95、q_min=0.10、χ=10、W=20、rate=120、垫资 150
  · 使用**修复后**的代码（est 刷新已恢复；刷新节奏 10 块 / 16 线程）

排序：**点位优先**（hl=1 的 5 轮跑完再跑 hl=2），这样每点做完即可出读数，
不必等整个 90 臂跑完。总时长约 19 机时（12.6 min/臂 × 90）。

产物：
  out/<ts>/hl_results.csv    每臂一行，跑完即追加（中途可查）
  out/<ts>/hl_report.json    每个点位的 5 轮聚合（中位/极值/散点 + gates 汇总）

用法：
  python hl_sweep.py --dry-run
  python hl_sweep.py                       # 全量（约 19 机时）
  python hl_sweep.py --hls 1,2 --reps 2    # 小样快验
"""
import argparse
import csv
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
CFG = HERE / "config.yaml"

HLS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 50]

# —— 修复后的运行参数（与 A1_fixed_baseline 一致，保证可比）——
FIXED = ["exp.refresh_every_blocks=10", "exp.refresh_workers=16",
         "exp.max_backlog=40000", "exp.drain_tail_s=900"]
# —— 4 万笔口径（覆盖 config.yaml 里的 10 万档）——
SCALE = ["exp.ctx_per_broker=800", "exp.pool_scan=200000",
         "exp.balances_eth=150", "exp.rate=120"]

CSV_COLS = ["hl", "rep", "arm", "relay", "events", "transfers", "moved_eth",
            "legs_share", "throughput", "wall_s", "gates_ok", "burn_ok",
            "aborted", "path"]


def med(v):
    return statistics.median(v) if v else None


def run_one(hl, rep, out_base, timeout_note=""):
    arm = f"topup@150@@0.95@@{hl}"
    sess = out_base / f"hl{hl}__r{rep}"
    sess.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(CFG),
           "--out-root", str(sess), "--set", f"exp.arms={arm}"]
    for x in SCALE + FIXED:
        cmd += ["--set", x]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dur = time.monotonic() - t0
    (sess / "driver.log").write_text(p.stdout + p.stderr)
    subs = sorted([d for d in sess.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime)
    sfile = (subs[-1] / "summary.json") if subs else None
    if not sfile or not sfile.exists():
        return {"hl": hl, "rep": rep, "arm": arm, "relay": None, "events": None,
                "transfers": None, "moved_eth": None, "legs_share": None,
                "throughput": None, "wall_s": round(dur), "gates_ok": False,
                "burn_ok": False, "aborted": f"rc={p.returncode} no summary",
                "path": str(sess)}
    a = json.loads(sfile.read_text())["arms"][0]
    t = a["tdr"]
    return {"hl": hl, "rep": rep, "arm": a["arm"], "relay": a["relayed"],
            "events": t["events_opened"], "transfers": t["transfers_done"],
            "moved_eth": round(t["moved_wei"] / 1e18, 1),
            "legs_share": a["legs"]["tdr_share_of_ctx_legs"],
            "throughput": a["throughput_ctx_per_s"], "wall_s": a["wall_ctx_s"],
            "gates_ok": all(a["gates"].values()),
            "burn_ok": a["burn_reconcile"]["ok"],
            "aborted": (a["coord"]["aborted"] or ""),
            "path": str(sfile.parent)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hls", default=None, help="逗号分隔的 hl 白名单")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    hls = [float(x) for x in args.hls.split(",")] if args.hls else list(HLS)
    out_base = HERE / "out" / f"hlsweep_{datetime.now():%Y%m%d_%H%M%S}"
    print(f"  exp011 hl 搜索 · 点位 {len(hls)} 个 × {args.reps} 轮 = "
          f"{len(hls) * args.reps} 臂跑 · N=40000 · 修复后代码")
    if args.dry_run:
        for h in hls:
            print(f"    hl={h:<4} × {args.reps} 轮")
        print(f"  预计 {len(hls) * args.reps * 12.6 / 60:.1f} 机时")
        return 0
    out_base.mkdir(parents=True)
    print(f"  out: {out_base}")

    csvf = out_base / "hl_results.csv"
    fh = open(csvf, "w", newline="")
    w = csv.DictWriter(fh, fieldnames=CSV_COLS)
    w.writeheader()
    all_rows = []
    t_start = time.monotonic()
    try:
        for h in hls:                       # 点位优先
            print(f"\n  ==== hl={h} ====", flush=True)
            for r in range(1, args.reps + 1):
                row = run_one(h, r, out_base)
                w.writerow(row); fh.flush(); all_rows.append(row)
                if row["relay"] is None:
                    print(f"    r{r}: !! {row['aborted']}", flush=True)
                else:
                    print(f"    r{r}: relay {row['relay']:>4} | 事件 {row['events']:>4} "
                          f"| 搬量 {row['moved_eth']:>10,.0f} | "
                          f"{'OK' if row['gates_ok'] and row['burn_ok'] else 'FAIL'}",
                          flush=True)
            done = sum(1 for x in all_rows if x["hl"] == h)
            el = (time.monotonic() - t_start) / 60
            tot = len(hls) * args.reps
            print(f"    [进度] {len(all_rows)}/{tot} 臂 | 已用 {el:.0f} min | "
                  f"预计总 {el / max(len(all_rows), 1) * tot:.0f} min", flush=True)
    finally:
        fh.close()

    # —— 聚合 ——
    groups = {}
    for x in all_rows:
        groups.setdefault(x["hl"], []).append(x)
    report = {"hls": hls, "reps": args.reps, "n_ctx": 40000,
              "fixed": FIXED, "scale": SCALE, "points": []}
    for h in sorted(groups):
        rs = groups[h]
        ok = [x for x in rs if x["gates_ok"] and x["burn_ok"]]
        rel = [x["relay"] for x in rs if x["relay"] is not None]
        mv = [x["moved_eth"] for x in rs if x["moved_eth"] is not None]
        tr = [x["transfers"] for x in rs if x["transfers"] is not None]
        report["points"].append({
            "hl": h, "n_runs": len(rs), "gates_all_ok": len(ok) == len(rs),
            "relay": rel, "relay_median": med(rel),
            "moved_eth": mv, "moved_median": med(mv),
            "transfers": tr, "transfers_median": med(tr)})
    (out_base / "hl_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str))

    print("\n" + "=" * 78)
    print(f"  {'hl':>4} {'relay 中位':>10} {'[min..max]':>14} {'搬量中位':>12} "
          f"{'搬运笔中位':>11} {'gates':>6}")
    for p in report["points"]:
        if p["relay_median"] is None:
            print(f"  {p['hl']:>4}  (无有效轮次)")
            continue
        print(f"  {p['hl']:>4} {p['relay_median']:>10} "
              f"{'[' + str(min(p['relay'])) + '..' + str(max(p['relay'])) + ']':>14} "
              f"{p['moved_median']:>12,.0f} {p['transfers_median']:>11,.0f} "
              f"{'OK' if p['gates_all_ok'] else 'FAIL':>6}")
    print(f"  report: {out_base / 'hl_report.json'}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
