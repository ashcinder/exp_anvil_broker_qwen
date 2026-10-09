#!/usr/bin/env python3
"""exp032 · 两条 U 曲线并排：hl（左）与 q_min（右），同一组基底配置。

基底 = 阶跃突变 + 垫资 45 + rate120 + 节流 10/16 + 4 万笔
       + ε=0.95，扫描参数以外的那个参数取近最优值。
产物：out/smile_both.png。数据来自 out/arm_*/<ts>/summary.json，不重跑链。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from brokerlab.plotting import COLOR_ALT, COLOR_RELAY, plt, save  # noqa: E402


def read(pattern, key_re, key_cast=float):
    out = {}
    for f in HERE.glob(f"out/{pattern}/*/summary.json"):
        m = key_re.search(f.parts[-3])
        if not m:
            continue
        a = json.loads(f.read_text())["arms"][0]
        out[key_cast(m.group(1))] = {
            "relay": a["relayed"],
            "moved": a["tdr"]["moved_wei"] / 1e18,
            "ok": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
        }
    return out


def panel(ax, data, xlabel, title, color, xscale="log"):
    xs = sorted(data)
    ax.plot(xs, [data[x]["relay"] for x in xs], "-o", color=color,
            markersize=5, linewidth=1.6, label="relay fallback")
    ax.set_xscale(xscale)
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("relay fallback (CTX)")
    ax.set_title(title)
    ax.grid(alpha=0.25, which="both", linewidth=0.5)
    best = min(xs, key=lambda x: data[x]["relay"])
    ax.annotate(f"minimum: {data[best]['relay']} @ {best}",
                (best, data[best]["relay"]), textcoords="offset points",
                xytext=(8, 12), fontsize=8, color=color,
                arrowprops=dict(arrowstyle="->", color=color, lw=0.8))
    for x in xs:
        ax.annotate(f"{data[x]['relay']:,}", (x, data[x]["relay"]),
                    textcoords="offset points", xytext=(0, 7),
                    fontsize=7, ha="center", color=color)
    return best


def main():
    hl = read("arm_K45q3_hl*", re.compile(r"hl(\d+)$"), int)
    qm = read("arm_M45_q*", re.compile(r"_q([0-9.]+)$"), float)

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.3))
    bh = panel(ax[0], hl, "EWMA half-life $h_\\ell$ (blocks)",
               "(a) sweep $h_\\ell$  ($q_{\\min}=0.3$)", COLOR_RELAY)
    bq = panel(ax[1], qm, "$q_{\\min}$ (target floor ratio)",
               "(b) sweep $q_{\\min}$  ($h_\\ell=20$)", COLOR_ALT)
    ax[0].set_xticks([4, 10, 20, 50, 150, 400])
    ax[0].set_xticklabels(["4", "10", "20", "50", "150", "400"])
    ax[0].set_ylim(80, 2500)
    ax[1].set_ylim(120, 60000)

    fig.suptitle("Both parameters show an interior optimum under the same load "
                 "(step shift + 45 ETH/shard)", fontsize=10)
    out = HERE / "out" / "smile_both.png"
    save(fig, out)
    print(f"  figure: {out}")
    print(f"  hl    minimum: {bh}   q_min minimum: {bq}")


if __name__ == "__main__":
    main()
