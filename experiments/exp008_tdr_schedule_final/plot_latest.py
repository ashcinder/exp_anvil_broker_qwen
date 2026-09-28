#!/usr/bin/env python3
"""Plot the newest complete exp008 report without hard-coded balance or arm tags."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab import plotting as P  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def newest_complete_report():
    for path in sorted(HERE.glob("out/*/report.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        status = data.get("status", {})
        arms = data.get("arms", {})
        if status.get("complete") and arms and all(
                a.get("gates_all_true") and a.get("burn_all_ok")
                for a in arms.values()):
            return path, data
    raise RuntimeError("no complete exp008 report with passing integrity gates")


def short_label(tag, arm):
    engine = arm.get("engine", tag.split("@", 1)[0])
    if engine == "plain":
        return "Plain"
    if engine == "valve":
        return "Valve"
    if engine == "proportional":
        return "Proportional"
    if "@20.0" in tag or "@20" in tag:
        return "EWMA Topup"
    return "Hard Topup"


def med(arm, field):
    return float(arm.get(field, {}).get("median", 0) or 0)


def main():
    path, report = newest_complete_report()
    items = list(report["arms"].items())
    labels = [short_label(tag, arm) for tag, arm in items]
    colors = [P.COLOR_GRAY, P.COLOR_ALT, P.COLOR_DATA,
              P.COLOR_RELAY, P.COLOR_BROKER][:len(items)]
    x = list(range(len(items)))
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), dpi=140)

    panels = [
        (axes[0, 0], "relay", "Relay fallbacks", "CTX"),
        (axes[0, 1], "events", "Rebalancing events", "events"),
        (axes[1, 0], "transfers", "Completed rebalancing transfers", "transfers"),
        (axes[1, 1], "throughput_ctx_per_s", "Completion throughput", "CTX/s"),
    ]
    for ax, field, title, ylabel in panels:
        values = [med(arm, field) for _, arm in items]
        bars = ax.bar(x, values, color=colors)
        ax.set_xticks(x, labels, rotation=18, ha="right")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.bar_label(bars, labels=[f"{v:,.2f}" if v % 1 else f"{v:,.0f}"
                                   for v in values], padding=3, fontsize=8)
        if field in {"events", "transfers"} and max(values, default=0) > 100:
            ax.set_yscale("symlog", linthresh=1)
            ax.set_ylim(0, max(values) * 2.2)
        else:
            ax.margins(y=0.16)

    exp = report["params"]["exp"]
    fig.suptitle(
        f"exp008 latest complete run: {path.parent.name} | "
        f"{report.get('ctx_total', 0):,} CTX/arm | balance={exp.get('balances_eth')} ETH",
        fontsize=11,
    )
    out = HERE / "figs" / "exp008_latest.png"
    out.parent.mkdir(exist_ok=True)
    P.save(fig, out)
    print(f"data: {path}")
    print(f"figure: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
