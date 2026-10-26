#!/usr/bin/env python3
"""exp011 · relay–hl smile curve at capital 45, three load shapes.

产物：out/smile_hl.png。数据来自 out/arm_*/<ts>/summary.json，不重跑链。
标签用英文：本机无 CJK 字体，且项目既有图（exp003 make_figure）也是英文。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from brokerlab.plotting import (COLOR_ALT, COLOR_GRAY,  # noqa: E402
                                COLOR_RELAY, plt, save)

SERIES = [
    ("step shift, canonical settings", "arm_K45_hl",
     COLOR_RELAY, "-", "o"),
    ("step shift, exploratory (rate=80)", "arm_S45_hl",
     "#B26A00", "--", "D"),
    ("periodic shift (hotspot alternates)", "arm_C45_hl",
     COLOR_ALT, "--", "s"),
    ("no shift (stationary load)", "arm_C45_noshift",
     COLOR_GRAY, ":", "^"),
]

HL = re.compile(r"(?:hl|noshift)(\d+)$")


def load(prefix):
    pts = {}
    for f in HERE.glob(f"out/{prefix}*/*/summary.json"):
        m = HL.search(f.parts[-3])
        if not m:
            continue
        a = json.loads(f.read_text())["arms"][0]
        pts[int(m.group(1))] = {
            "relay": a["relayed"],
            "moved": a["tdr"]["moved_wei"] / 1e18,
            "ok": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
        }
    return pts


def main():
    data = {lbl: load(p) for lbl, p, *_ in SERIES}

    fig, ax = plt.subplots(1, 2, figsize=(10.5, 4.0))
    for label, _p, color, ls, mk in SERIES:
        d = data[label]
        if not d:
            continue
        xs = sorted(d)
        ax[0].plot(xs, [d[x]["relay"] for x in xs], ls, marker=mk, color=color,
                   label=label, markersize=4.5, linewidth=1.5)
        ax[1].plot(xs, [d[x]["moved"] for x in xs], ls, marker=mk, color=color,
                   label=label, markersize=4.5, linewidth=1.5)
        best = min(xs, key=lambda x: d[x]["relay"])
        ax[0].annotate(f"min {d[best]['relay']} @ hl={best}",
                       (best, d[best]["relay"]), textcoords="offset points",
                       xytext=(6, 9), fontsize=7.5, color=color)

    for i, (ylab, title) in enumerate([
            ("relay fallback (CTX)", "(a) relay vs EWMA half-life: the smile"),
            ("moved volume (ETH)", "(b) moved volume vs half-life")]):
        ax[i].set_xscale("log")
        ax[i].set_xlabel("EWMA half-life $h_\\ell$ (blocks)")
        ax[i].set_ylabel(ylab)
        ax[i].set_title(title)
        ax[i].grid(alpha=0.25, which="both", linewidth=0.5)
        ax[i].legend(fontsize=7.5, loc="best")
        ax[i].set_xticks([4, 10, 20, 50, 150, 400])
        ax[i].set_xticklabels(["4", "10", "20", "50", "150", "400"])
    ax[0].set_yscale("log")
    ax[0].set_ylim(50, 1200)

    out = HERE / "out" / "smile_hl.png"
    save(fig, out)
    print(f"  figure: {out}")

    hls = sorted({x for d in data.values() for x in d})
    print(f"\n  {'hl':>5}" + "".join(f"{lbl.split(' (')[0]:>34}" for lbl, *_ in SERIES))
    for hl in hls:
        row = f"  {hl:>5}"
        for lbl, *_ in SERIES:
            d = data[lbl]
            row += f"{d[hl]['relay']:>34}" if hl in d else f"{'-':>34}"
        print(row)


if __name__ == "__main__":
    main()
