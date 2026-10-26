#!/usr/bin/env python3
"""exp011 · 最终图：闪烁负载下两条 U 曲线 + TDR 有效性。

数据源 out/arm_Y_*（n=3，交错设计）。产物 out/final_smile.png。
"""
import glob
import json
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from brokerlab.plotting import (COLOR_ALT, COLOR_GRAY, COLOR_RELAY,  # noqa: E402
                                plt, save)

OUT = HERE / "out"


def grab(pat, cast=float):
    got = {}
    for f in OUT.glob("arm_Y_*/*/summary.json"):
        m = re.search(pat, f.parts[-3])
        if not m:
            continue
        a = json.loads(f.read_text())["arms"][0]
        # 未过守恒门的整跑不进图（与 final_table.py 同一口径）
        if not (all(a["gates"].values()) and a["burn_reconcile"]["ok"]):
            continue
        # 键必须转成数值：字符串键会被 matplotlib 当成分类轴
        got.setdefault(cast(m.group(1)), []).append(a["relayed"])
    return {k: (st.median(v), min(v), max(v), len(v)) for k, v in got.items()}


def draw(ax, data, xlab, title, color, key=float, xscale="log"):
    xs = sorted(data, key=key)
    ys = [data[x][0] for x in xs]
    lo = [data[x][1] for x in xs]
    hi = [data[x][2] for x in xs]
    ax.fill_between(xs, lo, hi, color=color, alpha=0.18, linewidth=0)
    ax.plot(xs, ys, "-o", color=color, markersize=5, linewidth=1.7)
    ax.set_xscale(xscale)
    ax.set_yscale("log")
    ax.minorticks_off()
    ax.set_xlabel(xlab)
    ax.set_ylabel("relay fallback (CTX)")
    ax.set_title(title)
    ax.grid(alpha=0.25, which="both", linewidth=0.5)
    best = min(data, key=lambda x: data[x][0])
    ax.annotate(f"min {data[best][0]:.0f} @ {best}",
                (best, data[best][0]), textcoords="offset points",
                xytext=(8, 12), fontsize=8, color=color,
                arrowprops=dict(arrowstyle="->", color=color, lw=0.8))
    return best


def main():
    hl = grab(r"hl(\d+)_r\d", int)
    qm = grab(r"_q([0-9.]+)_r\d", float)

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
    bh = draw(ax[0], hl, "EWMA half-life $h_\\ell$ (blocks)",
              "(a) sweep $h_\\ell$  ($q_{\\min}$=0.3)", COLOR_RELAY, int)
    # q_min=0 不能上对数轴（log 0 无定义）⇒ 用线性轴
    bq = draw(ax[1], qm, "$q_{\\min}$ (target floor ratio)",
              "(b) sweep $q_{\\min}$  ($h_\\ell$=10)", COLOR_ALT, float,
              xscale="linear")
    ax[0].set_xticks([2, 4, 10, 20, 50, 150, 400])
    ax[0].set_xticklabels(["2", "4", "10", "20", "50", "150", "400"])
    ax[1].set_xticks([0, 0.1, 0.3, 0.5, 0.7, 0.9])
    ax[1].set_xticklabels(["0", "0.1", "0.3", "0.5", "0.7", "0.9"])
    ax[0].set_ylim(60, 500)
    ax[1].set_ylim(60, 80000)
    ax[0].set_xlim(1.7, 600)
    ax[1].set_xlim(-0.04, 0.98)

    # 样本数从数据里取（各点可能因剔除而未过门的整跑而略少）
    n_rep = max(max(v[3] for v in hl.values()), max(v[3] for v in qm.values()))
    fig.suptitle("Both parameters show an interior optimum under the regime-shift "
                 f"load (n={n_rep}, median with min–max band)", fontsize=10)
    out = OUT / "final_smile.png"
    save(fig, out)
    print(f"  figure: {out}")
    print(f"  hl min={bh}   q_min min={bq}")


if __name__ == "__main__":
    main()
