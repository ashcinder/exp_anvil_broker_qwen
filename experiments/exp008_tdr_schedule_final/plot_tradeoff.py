#!/usr/bin/env python3
"""Single-column exp008 tradeoff figure for an ACM sigconf paper."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import LogLocator, NullLocator


# Source: exp008; medians supplied for the 16-shard, 50-broker,
# 40,000-CTX, 150-ETH/shard, rate=120 comparison. Do not infer missing data.
DATA = {
    "plain": {"moved_eth": 0, "relay_ctx": 24841, "transfers": 0},
    "valve": {"moved_eth": 13045, "relay_ctx": 20863, "transfers": 273},
    "proportional": {"moved_eth": 813408, "relay_ctx": 110, "transfers": 22642},
    "topup ε=0.1": {"moved_eth": 807759, "relay_ctx": 72, "transfers": 17206},
    "topup ε=0.95": {"moved_eth": 211389, "relay_ctx": 17, "transfers": 1606},
    "final (topup+EWMA)": {"moved_eth": 163674, "relay_ctx": 20, "transfers": 1227},
}

BROKER = "#56B4E9"
ALT = "#009E73"
RELAY = "#E79016"
BASELINE = "#888888"
ORDER = (
    "valve",
    "proportional",
    "topup ε=0.1",
    "topup ε=0.95",
    "final (topup+EWMA)",
)


def main() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "pdf.fonttype": 42,
    })

    fig, ax = plt.subplots(figsize=(3.33, 3.10))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(1e3, 1e6)
    ax.set_ylim(5, 5e4)
    ax.set_xlabel("Cumulative rebalancing volume (ETH, log)")
    ax.set_ylabel("Relay fallbacks (CTX, log)")
    ax.xaxis.set_major_locator(LogLocator(base=10, numticks=4))
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=6))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_minor_locator(NullLocator())
    ax.tick_params(direction="out", length=2.5, width=0.6)
    for spine in ax.spines.values():
        spine.set_linewidth(0.6)

    # Highlight a desirable part of the tradeoff plane, behind all marks.
    ax.add_patch(Rectangle(
        (1e5, 5), 2e5, 45, facecolor=ALT, alpha=0.09,
        edgecolor="none", zorder=0,
    ))
    ax.text(1.12e5, 39, "good", color=ALT, fontsize=7,
            ha="left", zorder=1)

    # This reference is an externally supplied physical bound, not a data point.
    ax.axvline(164000, color="black", ls=":", lw=0.8, zorder=1)
    ax.text(1.58e5, 4.7e4, "physical lower bound\n(164k ETH)",
            ha="right", va="top", fontsize=6.4, color="black")

    # The true plain x value remains zero in DATA; its hollow mark is a
    # boundary glyph because zero has no coordinate on a logarithmic axis.
    plain = DATA["plain"]
    ax.scatter([1e3], [plain["relay_ctx"]], s=34, marker="o",
               facecolors="none", edgecolors=BASELINE, linewidths=1.1,
               zorder=5, clip_on=False)
    ax.plot([1e3, 1.65e3], [plain["relay_ctx"]] * 2,
            ls="--", lw=0.8, color=BASELINE, zorder=2)
    ax.annotate("plain: no rebalancing (0 ETH moved)",
                xy=(1.65e3, plain["relay_ctx"]), xytext=(0, 13),
                textcoords="offset points", ha="left", va="bottom",
                color=BASELINE, fontsize=6.7)

    # Connect the five nonzero-volume arms in the stated experimental order.
    for start, end in zip(ORDER, ORDER[1:]):
        a, b = DATA[start], DATA[end]
        ax.annotate("", xy=(b["moved_eth"], b["relay_ctx"]),
                    xytext=(a["moved_eth"], a["relay_ctx"]),
                    arrowprops={"arrowstyle": "->", "color": BASELINE,
                                "lw": 1, "shrinkA": 6, "shrinkB": 6},
                    zorder=2)

    colors = {
        "valve": BASELINE,
        "proportional": ALT,
        "topup ε=0.1": RELAY,
        "topup ε=0.95": ALT,
        "final (topup+EWMA)": BROKER,
    }
    offsets = {
        "valve": (5, -17, "left", "top"),
        "proportional": (-65, 15, "right", "bottom"),
        "topup ε=0.1": (-4, 12, "right", "bottom"),
        "topup ε=0.95": (6, -2, "right", "top"),
        "final (topup+EWMA)": (-30, 12, "right", "bottom"),
    }
    for name in ORDER:
        row = DATA[name]
        x, y = row["moved_eth"], row["relay_ctx"]
        ax.scatter([x], [y], s=57 if name.startswith("final") else 33,
                   color=colors[name], edgecolors="white", linewidths=0.4,
                   zorder=4)
        dx, dy, ha, va = offsets[name]
        name_dy = dy + 8 if va == "bottom" else dy
        count_dy = dy if va == "bottom" else dy - 8
        ax.annotate(name, xy=(x, y), xytext=(dx, name_dy),
                    textcoords="offset points", ha=ha, va=va,
                    fontsize=6.8, color="black", zorder=6)
        ax.annotate(f"{row['transfers']:,} transfers", xy=(x, y),
                    xytext=(dx, count_dy), textcoords="offset points",
                    ha=ha, va=va, fontsize=6.0, color="black", zorder=6)

    legend = [
        Line2D([], [], marker="o", linestyle="none", color=BROKER,
               markersize=6, label="final"),
        Line2D([], [], marker="o", linestyle="none", color=ALT,
               markersize=5, label="intermediate"),
        Line2D([], [], marker="o", linestyle="none", color=BASELINE,
               markerfacecolor="none", markersize=5, label="baseline"),
    ]
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(0.02, 0.75),
              frameon=False,
              handletextpad=0.3, borderpad=0.1, labelspacing=0.3)

    fig.tight_layout()
    out = Path(__file__).resolve().parent / "figs"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "exp008_tradeoff.pdf")
    fig.savefig(out / "exp008_tradeoff.png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main()
