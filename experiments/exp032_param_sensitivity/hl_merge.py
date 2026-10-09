#!/usr/bin/env python3
"""exp032 · 合并多轮 hl 搜索的原始数据 → 统一点位统计（供箱线图）。

hl_sweep.py 每次跑都会新开 out/hlsweep_<ts>/，本脚本把所有批次的
hl_results.csv 合起来，按 hl 汇总 n 轮（当前两批共 10 轮/点），输出：
  · hl_merged.csv   带 batch 列的统一明细（每行 = 一轮），画箱线图的直接输入
  · hl_merged.json  每点的 n / 中位 / 极值 / 四分位（箱线图五数概括）

用法：
  python hl_merge.py            # 合并全部批次
  python hl_merge.py --out x.json
"""
import argparse
import csv
import glob
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def q(vals, p):
    """线性插值分位（与 matplotlib 箱线图口径一致）。"""
    s = sorted(vals)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    files = sorted(glob.glob(str(HERE / "out" / "hlsweep_*" / "hl_results.csv")))
    if not files:
        print("  找不到任何 hlsweep_*/hl_results.csv")
        return 1
    rows = []
    for f in files:
        batch = Path(f).parent.name
        for r in csv.DictReader(open(f)):
            if r["relay"] in (None, "", "None"):
                continue
            r["batch"] = batch
            rows.append(r)
    print(f"  合并 {len(files)} 个批次，共 {len(rows)} 轮")
    for f in files:
        n = sum(1 for r in rows if Path(f).parent.name == r["batch"])
        print(f"    {Path(f).parent.name}: {n} 轮")

    out_csv = HERE / "out" / "hl_merged.csv"
    cols = ["batch"] + [c for c in rows[0] if c != "batch"]
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    by = {}
    for r in rows:
        by.setdefault(float(r["hl"]), []).append(r)
    points = []
    for h in sorted(by):
        rs = by[h]
        rel = [int(x["relay"]) for x in rs]
        mv = [float(x["moved_eth"]) for x in rs]
        tr = [int(x["transfers"]) for x in rs]
        ev = [int(x["events"]) for x in rs]
        ok = all(x["gates_ok"] == "True" and x["burn_ok"] == "True" for x in rs)
        points.append({
            "hl": h, "n": len(rs), "gates_all_ok": ok,
            "relay": {"median": statistics.median(rel), "min": min(rel),
                      "max": max(rel), "q1": q(rel, .25), "q3": q(rel, .75),
                      "values": rel},
            "moved_eth": {"median": statistics.median(mv), "min": min(mv),
                          "max": max(mv), "q1": q(mv, .25), "q3": q(mv, .75),
                          "values": [round(v, 1) for v in mv]},
            "transfers": {"median": statistics.median(tr), "values": tr},
            "events": {"median": statistics.median(ev), "values": ev},
        })
    out_json = Path(args.out) if args.out else HERE / "out" / "hl_merged.json"
    out_json.write_text(json.dumps(
        {"batches": [Path(f).parent.name for f in files], "points": points},
        indent=2, ensure_ascii=False))

    print(f"\n  {'hl':>4} {'n':>3} {'relay 中位':>10} {'[min..max]':>12} "
          f"{'搬量中位':>11} {'[Q1..Q3]':>22} gates")
    for p in points:
        r, m = p["relay"], p["moved_eth"]
        print(f"  {p['hl']:>4} {p['n']:>3} {r['median']:>10} "
              f"{'[' + str(r['min']) + '..' + str(r['max']) + ']':>12} "
              f"{m['median']:>11,.0f} "
              f"{'[' + format(m['q1'], ',.0f') + '..' + format(m['q3'], ',.0f') + ']':>22} "
              f"{'OK' if p['gates_all_ok'] else 'FAIL'}")
    print(f"\n  merged: {out_csv}\n  stats : {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
