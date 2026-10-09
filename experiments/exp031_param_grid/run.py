#!/usr/bin/env python3
"""exp031 · 定稿方法参数网格全量搜索（编排器）。

原则：不改任何其它包的代码；一切测量都发生在 20260925 出块竞态修复之后。
每个"小实验"= 一批次 × 一场（同场共享新链、同流同 seed，只换处理）。
目录命名（用户要求参数写进名字）：
    out/<ts>/<批次id>__r<场次>/<exp003 时间戳>/<方案token>/
  方案 token 即参数：topup@150.0@0.95@20.0 = topup, fund150, ε0.95, hl20。
增量产物：
    out/<ts>/grid_results.csv    每臂一行（跑完即追加，可中途查看）
    out/<ts>/grid_report.json    聚合：批次→方案→跨场中位/极值/散点
用法：
    python run.py --dry-run
    python run.py --only A1_eps_grid_matrix,B1_qmin_grid
    python run.py
"""
import argparse
import csv
import json
import re
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

OUT_RE = re.compile(r"out:\s*(\S+?/out/\S+?)\s")
ETH = 10 ** 18

CSV_COLS = ["batch", "rep", "arm", "engine", "fund_eth", "rate", "n_ctx",
            "relay", "events", "transfers", "moved_eth", "legs_share",
            "throughput", "wall_s", "gates_ok", "burn_ok", "aborted", "path"]


def med(v):
    return statistics.median(v) if v else None


def run_session(bid, k, batch, scales, cfg_path, out_base):
    """跑一批次的一场。返回 [每臂的结果行]。异常不抛出：记 aborted 行。"""
    session = out_base / f"{bid}__r{k}"
    session.mkdir(parents=True, exist_ok=True)
    sets = list(scales[batch["scale"]])
    sets.append("exp.arms=" + ",".join(batch["arms"]))
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(cfg_path),
           "--out-root", str(session)]
    for x in sets:
        cmd += ["--set", x]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    (session / "driver.log").write_text(p.stdout + p.stderr)
    dur = time.monotonic() - t0
    rows = []
    # 找 exp003 在 session 下建的时间戳目录
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
    print(f"  [{bid} r{k}] {dur/60:.1f} min, {len(rows)} 臂, "
          f"rc={p.returncode}", flush=True)
    return rows


def aggregate(out_base, all_rows, cfg):
    by = {}
    for r in all_rows:
        key = (r["batch"], r["arm"])
        by.setdefault(key, []).append(r)
    report = {"params": {"profile": "exp010", "batches": list(
                  dict(cfg.exp["batches"]).keys())}, "groups": []}
    for (bid, arm), rs in by.items():
        ok = [r for r in rs if r["gates_ok"] and r["burn_ok"]]
        g = {"batch": bid, "arm": arm, "n_runs": len(rs),
             "gates_all_ok": len(ok) == len(rs),
             "relay": [r["relay"] for r in rs],
             "transfers": [r["transfers"] for r in rs],
             "moved_eth": [r["moved_eth"] for r in rs]}
        if all(isinstance(v, (int, float)) for v in g["relay"]):
            g["relay_median"] = med(g["relay"])
            g["transfers_median"] = med(g["transfers"])
            g["moved_eth_median"] = med(g["moved_eth"])
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
    scales = {"formal": cfg.exp["scale_formal"], "matrix": cfg.exp["scale_matrix"]}
    only = set(args.only.split(",")) if args.only else None
    out_base = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.dry_run:
        out_base.mkdir(parents=True)
    print(f"  exp031 · out: {out_base}")

    if args.dry_run:
        for bid, b in batches.items():
            if only and bid not in only:
                continue
            est = len(b["arms"]) * b["reps"]
            print(f"  [{bid}] scale={b['scale']} reps={b['reps']} "
                  f"arms={len(b['arms'])} → {est} 臂跑")
            for a in b["arms"]:
                print(f"      - {a}")
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
            print(f"\n  ==== 批次 {bid}（{b['scale']}，{b['reps']} 场）====",
                  flush=True)
            for k in range(1, int(b["reps"]) + 1):
                rows = run_session(bid, k, b, scales, HERE / "config.yaml",
                                   out_base)
                for r in rows:
                    writer.writerow(r)
                    all_rows.append(r)
                csv_handle.flush()     # 逐场落盘：中途可查、崩溃不丢已完成行
    finally:
        csv_handle.close()
    report = aggregate(out_base, all_rows, cfg)
    print("\n" + "=" * 70)
    for g in report["groups"]:
        if isinstance(g.get("relay_median"), (int, float)):
            print(f"  {g['batch']:<22} {g['arm']:<26} "
                  f"relay {g['relay']} → 中位 {g['relay_median']} | "
                  f"gates {'OK' if g['gates_all_ok'] else 'FAIL'}")
    print(f"  grid_report: {out_base / 'grid_report.json'}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
