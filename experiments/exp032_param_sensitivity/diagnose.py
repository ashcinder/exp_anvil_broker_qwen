#!/usr/bin/env python3
"""exp032 · 吞吐瓶颈诊断（不改语义，只量读数）。

背景：10W 定稿臂实测吞吐 ~67 CTX/s，而全机 CPU 只用 25%、单笔延迟已是物理下限
（1 块/段、hops≈2）。吞吐 = 并发 ÷ 延迟 ⇒ 卡点在"并发加不上去"。两个嫌疑：
  ① `sender_window`（每 (sender,src) 在途上限）撞上"流按发送者分块排序"
     —— 释放前缀里 key 极少，早期每块只覆盖 2-4 个 key；
  ② `rate`（每块释放量）太小，pend 里活跃 key 不够。

做法：小 N（默认 25000，约 8 min/臂）跑一个 sender_window × rate 的对照，
并开 EXP003_TELEMETRY 让协调器每块回报 [块龄, 派发, backlog, pend键数, 未占满键数,
循环次数]——把"谁卡住"从猜测变成读数。

判读：
  · 若提高 window 后吞吐显著上升 ⇒ ① 坐实，窗口是手刹；
  · 若提高 rate 后吞吐上升 ⇒ ② 成立（更多 key 被提前暴露）；
  · 若两者都不动 ⇒ 瓶颈在别处（引擎/链/循环），看 telemetry 的 pend键数与循环次数。

用法：
  python diagnose.py                      # 默认 4 组对照
  python diagnose.py --n 25000 --arms "sw2r120,sw8r120"
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"
BASE_CFG = HERE.parent / "exp008_tdr_schedule_final" / "config.yaml"
ARM = "topup@150@@0.95@@20"

# (标签, sender_window, rate)
MATRIX = [("sw2_r120", 2, 120), ("sw4_r120", 4, 120), ("sw8_r120", 8, 120),
          ("sw2_r400", 2, 400), ("sw8_r400", 8, 400),
          # 第二轮：探天花板。第一轮里 window=8 时"可用键"仍为 0 ⇒ 窗口还是手刹，
          # 往上继续加，直到吞吐不再涨（那时才是真正的天花板）。
          ("sw16_r400", 16, 400), ("sw32_r400", 32, 400),
          ("sw64_r400", 64, 400)]


def run_one(label, sw, rate, n_ctx, base_out, extra_backlog):
    sess = base_out / label
    sess.mkdir(parents=True, exist_ok=True)
    # pool_scan 要随 N 放大（实测 200000 只能凑出 71935 笔，不够 10W）
    pool = max(200000, n_ctx * 5)
    sets = [f"exp.arms={ARM}", f"exp.rate={rate}",
            f"exp.sender_window={sw}", f"exp.ctx_per_broker={n_ctx // 50}",
            f"exp.pool_scan={pool}", "exp.balances_eth=150",
            f"exp.max_backlog={extra_backlog}", "exp.drain_tail_s=900"]
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(BASE_CFG),
           "--out-root", str(sess)]
    for x in sets:
        cmd += ["--set", x]
    env = dict(os.environ, EXP003_TELEMETRY="1")
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    dur = time.monotonic() - t0
    (sess / "driver.log").write_text(p.stdout + p.stderr)
    subs = sorted([d for d in sess.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime)
    sfile = (subs[-1] / "summary.json") if subs else None
    if not sfile or not sfile.exists():
        return {"label": label, "window": sw, "rate": rate,
                "error": f"no summary rc={p.returncode}"}
    d = json.loads(sfile.read_text())
    a = d["arms"][0]
    co = json.loads((sfile.parent / a["arm"] / "coordinator.json").read_text())
    tel = co.get("telemetry") or []
    # 稳态 = 跳过前 15% 的块（冷启动）
    cut = int(len(tel) * 0.15)
    st = tel[cut:] or tel
    def med(i):
        v = [r[i] for r in st]
        return statistics.median(v) if v else None
    # 每块派发数 = 累计派发的一阶差分
    disp_per_blk = [st[i][1] - st[i - 1][1] for i in range(1, len(st))]
    return {
        "label": label, "window": sw, "rate": rate, "wall_min": round(dur / 60, 1),
        "throughput": a["throughput_ctx_per_s"], "relay": a["relayed"],
        "backlog_peak": co.get("backlog_peak"), "aborted": co.get("aborted"),
        "gates_ok": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
        "steady_disp_per_blk": statistics.median(disp_per_blk) if disp_per_blk else None,
        "steady_pend_keys": med(3), "steady_ready_keys": med(4),
        "steady_iters_per_blk": med(5), "blocks": len(tel),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25000)
    ap.add_argument("--arms", default=None, help="逗号分隔标签白名单")
    ap.add_argument("--backlog", type=int, default=30000)
    args = ap.parse_args()
    only = set(args.arms.split(",")) if args.arms else None
    out_base = HERE / "out" / f"diag_{datetime.now():%Y%m%d_%H%M%S}"
    out_base.mkdir(parents=True)
    todo = [m for m in MATRIX if not only or m[0] in only]
    print(f"  exp032 瓶颈诊断 · out: {out_base} | N={args.n} | 臂={ARM}")
    print(f"  对照组: {[m[0] for m in todo]}")
    rows = []
    for label, sw, rate in todo:
        print(f"\n  ---- {label} (sender_window={sw}, rate={rate}) ----", flush=True)
        r = run_one(label, sw, rate, args.n, out_base, args.backlog)
        rows.append(r)
        if r.get("error"):
            print(f"  {label}: !! {r['error']}", flush=True)
        else:
            print(f"  {label}: {r['wall_min']}min | {r['throughput']} CTX/s | "
                  f"稳态每块派发 {r['steady_disp_per_blk']} | "
                  f"pend键 {r['steady_pend_keys']} 可用键 {r['steady_ready_keys']} | "
                  f"每块循环 {r['steady_iters_per_blk']} | backlog {r['backlog_peak']} | "
                  f"{'OK' if r['gates_ok'] else 'FAIL'}", flush=True)
        (out_base / "diag_report.json").write_text(json.dumps(
            {"n_ctx": args.n, "rows": rows}, indent=2, ensure_ascii=False))
    print("\n" + "=" * 88)
    print(f"  {'组':<10} {'win':>4} {'rate':>5} {'CTX/s':>7} {'稳态派发/块':>11} "
          f"{'pend键':>7} {'可用键':>7} {'循环/块':>8} {'backlog':>8}")
    for r in rows:
        if r.get("error"):
            print(f"  {r['label']:<10} {r['window']:>4} {r['rate']:>5}   ERROR")
            continue
        print(f"  {r['label']:<10} {r['window']:>4} {r['rate']:>5} {r['throughput']:>7} "
              f"{str(r['steady_disp_per_blk']):>11} {str(r['steady_pend_keys']):>7} "
              f"{str(r['steady_ready_keys']):>7} {str(r['steady_iters_per_blk']):>8} "
              f"{str(r['backlog_peak']):>8}")
    print(f"  report: {out_base / 'diag_report.json'}")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
