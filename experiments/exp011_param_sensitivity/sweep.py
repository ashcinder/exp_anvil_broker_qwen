#!/usr/bin/env python3
"""exp011 · 参数敏感性网格（编排器）。

与 exp010/run.py 同构（复用 exp003 管线，不改任何其它包），差别：
  · 全部批次都跑 10W 正式档（config.yaml 已随档调好，不再分 formal/matrix 两套）；
  · 网格密度为每参数 20 点位（ε / q_min / hl），见 config.yaml 的 exp.batches。

原则（沿 exp010）：seed 全批次冻结（噪声=时序竞态，非流差异）；每臂照常过
gates（G1-G4 + burn≡mint），任何 False 都保留现场不掩盖；某场崩溃记 aborted 行、
批次继续（单场失败不废整批）；逐场追加 CSV，中途可查、崩溃不丢已完成行。

用法：
  python sweep.py --dry-run                       # 看计划（臂数/场数/估算机时）
  python sweep.py --only E1_eps_grid              # 只跑一组（或逗号分隔多组）
  python sweep.py                                 # 全网格（≈ 180 臂跑，串行）

产物：
  out/<ts>/grid_results.csv    每臂一行（跑完即追加）
  out/<ts>/grid_report.json    聚合：批次→方案→跨场中位/极值/散点
  out/<ts>/<批次id>__r<场次>/<exp003 时间戳>/<方案token>/
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
sys.path.insert(0, str(ROOT))
from brokerlab.config import apply_overrides, load_config  # noqa: E402

ETH = 10 ** 18
CSV_COLS = ["batch", "rep", "arm", "engine", "fund_eth", "rate", "n_ctx",
            "relay", "events", "transfers", "moved_eth", "legs_share",
            "throughput", "wall_s", "gates_ok", "burn_ok", "aborted", "path"]


def med(v):
    return statistics.median(v) if v else None


def run_session(bid, k, batch, cfg_path, out_base):
    """跑一批次的一场。返回每臂结果行；异常不抛出，记 aborted 行。"""
    session = out_base / f"{bid}__r{k}"
    session.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(cfg_path),
           "--out-root", str(session),
           "--set", "exp.arms=" + ",".join(batch["arms"])]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    (session / "driver.log").write_text(p.stdout + p.stderr)
    dur = time.monotonic() - t0
    rows = []
    sub = sorted([d for d in session.iterdir() if d.is_dir()],
                 key=lambda d: d.stat().st_mtime)
    sfile = (sub[-1] / "summary.json") if sub else session / "summary.json"
    if not sfile.exists():
        return [{"batch": bid, "rep": k, "arm": "(whole)", "engine": "-",
                 "fund_eth": "-", "rate": "-", "n_ctx": "-", "relay": "-",
                 "events": "-", "transfers": "-", "moved_eth": "-",
                 "legs_share": "-", "throughput": "-",
                 "wall_s": round(dur), "gates_ok": False, "burn_ok": False,
                 "aborted": f"rc={p.returncode} no summary",
                 "path": str(session)}]
    d = json.loads(sfile.read_text())
    for a in d["arms"]:
        t = a["tdr"]
        rows.append({
            "batch": bid, "rep": k, "arm": a["arm"], "engine": a["engine"],
            "fund_eth": a["fund_eth"], "rate": a["rate"], "n_ctx": a["n"],
            "relay": a["relayed"], "events": t["events_opened"],
            "transfers": t["transfers_done"],
            "moved_eth": round(t["moved_wei"] / ETH, 1),
            "legs_share": a["legs"]["tdr_share_of_ctx_legs"],
            "throughput": a["throughput_ctx_per_s"],
            "wall_s": a["wall_ctx_s"],
            "gates_ok": all(a["gates"].values()),
            "burn_ok": a["burn_reconcile"]["ok"],
            "aborted": (a["coord"]["aborted"] or ""),
            "path": str(sfile.parent / a["arm"]),
        })
    print(f"  [{bid} r{k}] {dur / 60:.1f} min, {len(rows)} 臂, "
          f"rc={p.returncode}", flush=True)
    return rows


def aggregate(out_base, all_rows):
    by = {}
    for r in all_rows:
        by.setdefault((r["batch"], r["arm"]), []).append(r)
    report = {"params": {"profile": "exp011"}, "groups": []}
    for (bid, arm), rs in by.items():
        ok = [r for r in rs if r["gates_ok"] and r["burn_ok"]]
        vals = [r["relay"] for r in rs]
        g = {"batch": bid, "arm": arm, "n_runs": len(rs),
             "gates_all_ok": len(ok) == len(rs),
             "relay": vals,
             "transfers": [r["transfers"] for r in rs],
             "moved_eth": [r["moved_eth"] for r in rs]}
        if all(isinstance(v, (int, float)) for v in vals):
            g["relay_median"] = med(vals)
            g["transfers_median"] = med([r["transfers"] for r in rs])
            g["moved_eth_median"] = med([r["moved_eth"] for r in rs])
        report["groups"].append(g)
    (out_base / "grid_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--only", default=None, help="逗号分隔批次 id 白名单")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    batches = cfg.exp["batches"]
    only = set(args.only.split(",")) if args.only else None
    out_base = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.dry_run:
        out_base.mkdir(parents=True)
    print(f"  exp011 sweep · out: {out_base} | rate={cfg.exp.get('rate')} "
          f"| N={50 * int(cfg.exp.get('ctx_per_broker', 0))}")

    if args.dry_run:
        tot = 0
        for bid, b in batches.items():
            if only and bid not in only:
                continue
            est = len(b["arms"]) * int(b["reps"])
            tot += est
            print(f"  [{bid}] reps={b['reps']} arms={len(b['arms'])} → {est} 臂跑")
        print(f"  合计 {tot} 臂跑（串行）")
        return 0

    csvf = out_base / "grid_results.csv"
    csv_handle = open(csvf, "w", newline="")
    writer = csv.DictWriter(csv_handle, fieldnames=CSV_COLS)
    writer.writeheader()
    all_rows = []
    try:
        for bid, b in batches.items():
            if only and bid not in only:
                continue
            print(f"\n  ==== 批次 {bid}（{b['reps']} 场 × {len(b['arms'])} 臂）====",
                  flush=True)
            for k in range(1, int(b["reps"]) + 1):
                rows = run_session(bid, k, b, HERE / "config.yaml", out_base)
                for r in rows:
                    writer.writerow(r)
                    all_rows.append(r)
                csv_handle.flush()     # 逐场落盘：中途可查、崩溃不丢已完成行
    finally:
        csv_handle.close()
    report = aggregate(out_base, all_rows)
    print("\n" + "=" * 70)
    for g in report["groups"]:
        if isinstance(g.get("relay_median"), (int, float)):
            print(f"  {g['batch']:<18} {g['arm']:<28} "
                  f"relay {g['relay']} → 中位 {g['relay_median']} | "
                  f"gates {'OK' if g['gates_all_ok'] else 'FAIL'}")
    print(f"  grid_report: {out_base / 'grid_report.json'}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
