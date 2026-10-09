#!/usr/bin/env python3
"""exp032 · 注入速率定标 —— 实测【本机】最大安全 trace 注入速率。

背景：文档里的 rate=120 来自另一台机器（exp003 档案 §8：6 核机上限定在
80-85 CTX/s）。本包一切实验都在本机跑，速率必须在本机重测（用户 2026-09-28 裁定）。

做法：不新增机制——直接用 exp008 的正式档 config 驱动 exp003 管线，只覆盖规模
（N / pool_scan / max_backlog / drain_tail_s）与 rate，扫一列速率档。
定标臂用**最重的生产臂** `topup@150@@0.95@@20`（带 TDR 的 coordinator 代铸负载），
它通过的速率对所有臂都安全——比 exp003 §8 用 plain 定标更保守。

判据（四条全过才算安全档）：
  ① coord.aborted is None          （backlog 未越限、deadline 未触顶）
  ② gates 全 True 且 burn≡mint     （物理正确性没被高负载破坏）
  ③ backlog_peak < 0.6 × max_backlog（排队留余量，不是贴边过）
  ④ RSS 峰值 < rss_cap_mb          （机器没被压垮）
max_safe_rate = 满足全部判据的最大 rate。

为什么要串行：档案 §8 教训——两个 run 并行会互相拖慢，导致 deadline 假超时。

用法：
  python calibrate_rate.py --rates 120,200,300
  python calibrate_rate.py --rates 160 --out-root out/calib_xxx
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
# 正式档基底：16 分片 × 50 broker × 150 ETH/片（exp008 定稿口径）
BASE_CFG = HERE.parent / "exp008_tdr_schedule_final" / "config.yaml"
CALIB_ARM = "topup@150@@0.95@@20"

sys.path.insert(0, str(ROOT))


def run_one(rate: int, base_out: Path, cfg_path: Path, extra: list) -> dict:
    """跑一个速率档，返回该档的判据摘要。异常/崩溃不抛出，记 error 行。"""
    sess = base_out / f"rate_{rate}"
    sess.mkdir(parents=True, exist_ok=True)
    sets = [f"exp.rate={rate}", f"exp.arms={CALIB_ARM}"] + list(extra)
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(cfg_path),
           "--out-root", str(sess)]
    for x in sets:
        cmd += ["--set", x]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dur = time.monotonic() - t0
    (sess / "driver.log").write_text(p.stdout + p.stderr)
    subs = sorted([d for d in sess.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime)
    sfile = (subs[-1] / "summary.json") if subs else None
    if sfile is None or not sfile.exists():
        return {"rate": rate, "rc": p.returncode, "wall_min": round(dur / 60, 1),
                "error": f"no summary (rc={p.returncode})", "safe": False,
                "path": str(sess)}
    d = json.loads(sfile.read_text())
    a = d["arms"][0]
    rss = d["params"].get("_runtime", {}).get("rss_peak_mb")
    backlog = a["coord"]["backlog_peak"]
    backlog_cap = float(extra_backlog(extra))
    gates_ok = all(a["gates"].values()) and a["burn_reconcile"]["ok"]
    safe = (a["coord"]["aborted"] is None and gates_ok
            and backlog < 0.6 * backlog_cap
            and (rss is None or rss < 9000))
    return {
        "rate": rate, "rc": p.returncode, "wall_min": round(dur / 60, 1),
        "throughput": a["throughput_ctx_per_s"], "relay": a["relayed"],
        "transfers": a["tdr"]["transfers_done"],
        "backlog_peak": backlog, "backlog_frac": round(backlog / backlog_cap, 3),
        "rss_peak_mb": rss, "gates_ok": gates_ok,
        "aborted": a["coord"]["aborted"],
        "no_qualified_local": a["coord"]["no_qualified_local"],
        "safe": bool(safe), "path": str(sfile.parent),
    }


def extra_backlog(extra: list) -> str:
    for x in extra:
        if x.startswith("exp.max_backlog="):
            return x.split("=", 1)[1]
    return "20000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", required=True,
                    help="逗号分隔的速率档（CTX/逻辑块），建议升序")
    ap.add_argument("--config", default=str(BASE_CFG))
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--ctx-per-broker", type=int, default=2000)
    ap.add_argument("--pool-scan", type=int, default=500000)
    ap.add_argument("--max-backlog", type=int, default=50000)
    ap.add_argument("--drain-tail-s", type=float, default=900.0)
    ap.add_argument("--balances-eth", type=float, default=150.0)
    args = ap.parse_args()

    rates = [int(x) for x in args.rates.split(",") if x.strip()]
    out_base = (Path(args.out_root) if args.out_root
                else HERE / "out" / f"calib_{datetime.now():%Y%m%d_%H%M%S}")
    out_base.mkdir(parents=True, exist_ok=True)
    extra = [f"exp.ctx_per_broker={args.ctx_per_broker}",
             f"exp.pool_scan={args.pool_scan}",
             f"exp.max_backlog={args.max_backlog}",
             f"exp.drain_tail_s={args.drain_tail_s}",
             f"exp.balances_eth={args.balances_eth}"]
    print(f"  exp032 速率定标 · out: {out_base}")
    print(f"  臂={CALIB_ARM} | N={50 * args.ctx_per_broker} | 档={rates}")
    print(f"  覆盖：{' '.join(extra)}")
    rows = []
    for r in rates:
        print(f"\n  ---- rate={r} ----", flush=True)
        row = run_one(r, out_base, Path(args.config), extra)
        rows.append(row)
        if row.get("error"):
            print(f"  rate={r}: !! {row['error']}", flush=True)
        else:
            print(f"  rate={r}: {row['wall_min']} min | "
                  f"吞吐 {row['throughput']} CTX/s | relay {row['relay']} | "
                  f"搬运 {row['transfers']} | backlog {row['backlog_peak']} "
                  f"({row['backlog_frac']:.0%}) | RSS {row['rss_peak_mb']} MB | "
                  f"gates {'OK' if row['gates_ok'] else 'FAIL'} | "
                  f"{'SAFE' if row['safe'] else 'NOT-SAFE'}", flush=True)
            if row["aborted"]:
                print(f"       aborted: {row['aborted']}", flush=True)
        (out_base / "rate_report.json").write_text(json.dumps(
            {"calib_arm": CALIB_ARM, "overrides": extra, "rows": rows,
             "max_safe_rate": max([x["rate"] for x in rows if x.get("safe")],
                                  default=None)},
            indent=2, ensure_ascii=False))
    print("\n" + "=" * 74)
    print(f"  {'rate':>6} {'wall(min)':>10} {'CTX/s':>8} {'backlog':>9} "
          f"{'RSS(MB)':>8}  verdict")
    for x in rows:
        if x.get("error"):
            print(f"  {x['rate']:>6} {'-':>10} {'-':>8} {'-':>9} {'-':>8}  ERROR")
            continue
        print(f"  {x['rate']:>6} {x['wall_min']:>10} {x['throughput']:>8} "
              f"{x['backlog_peak']:>9} {str(x['rss_peak_mb']):>8}  "
              f"{'SAFE' if x['safe'] else 'NOT-SAFE'}")
    safe = [x["rate"] for x in rows if x.get("safe")]
    print(f"  本机最大安全注入速率 ≈ {max(safe) if safe else '（本次无 SAFE 档，需下探）'}")
    print(f"  report: {out_base / 'rate_report.json'}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
