#!/usr/bin/env python3
"""Publication-style EWMA one-factor sensitivity figure from saved exp019 data.

This script reads completed result files only. It never starts Anvil or runs
transaction experiments. It uses the manuscript palette in brokerlab.plotting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab import plotting as P  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402


PANELS = (
    ("epsilon", "(a) Deficit threshold $\\epsilon$", "$\\epsilon$"),
    ("ewma_window_blocks", "(b) Sliding demand window", "Window size (blocks)"),
    ("ewma_half_life", "(c) EWMA half-life", "Half-life (blocks)"),
)
METRICS = (
    ("target_relay", "Relay fallbacks (CTX / 10,000)", P.COLOR_RELAY),
    ("target_transfers", "Completed TDR transfers", P.COLOR_BROKER),
)
BASELINE_KEYS = {
    "epsilon": "ewma_epsilon",
    "ewma_window_blocks": "ewma_window_blocks",
    "ewma_half_life": "ewma_half_life",
}
REFERENCE_COLORS = {"transfers": "#DE2B03", "relay": "#28A8F2"}
REFERENCE_STEMS = {
    "epsilon": "fig6a_epsilon_exp019",
    "ewma_window_blocks": "fig6b_window_exp019",
    "ewma_half_life": "fig6d_half_life_exp019",
}
REFERENCE_XLABELS = {
    "epsilon": "Rebalancing threshold ($\\epsilon$)",
    "ewma_window_blocks": "EWMA sliding window (blocks)",
    "ewma_half_life": "EWMA half-life (blocks)",
}


def latest_run() -> Path:
    runs = sorted(p.parent for p in (HERE / "out").glob("*/manifest.json"))
    if not runs:
        raise SystemExit("No exp019 run manifest found; pass --run explicitly.")
    return runs[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def source_data(run: Path):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    rows = read_csv(run / "parameter_value_impacts.csv")
    baseline = next(
        trial["state"] for trial in manifest["trials"]
        if any(view["kind"] == "baseline" for view in trial["views"]))
    expected: dict[str, set[float]] = {name: set() for name, _, _ in PANELS}
    for trial in manifest["trials"]:
        for view in trial["views"]:
            if view["kind"] == "oat" and view["name"] in expected:
                expected[view["name"]].add(float(view["x_value"]))
    if not all(expected.values()) or "ewma_window_blocks" not in baseline:
        raise ValueError(
            "This run does not contain the finite EWMA sliding-window sweep. "
            "The old warm-up sweep cannot be relabelled as a window sweep.")

    observed: dict[str, dict[float, dict[str, str]]] = {
        name: {} for name, _, _ in PANELS
    }
    for row in rows:
        name = row.get("parameter")
        if name not in observed or row.get("all_gates_ok", "").lower() != "true":
            continue
        x = number(row.get("value"))
        if not math.isfinite(x) or x in observed[name]:
            raise ValueError(f"Duplicate or invalid {name} value: {row.get('value')}")
        observed[name][x] = row
    return manifest, baseline, expected, observed


def replicate_samples(run: Path, manifest: dict, observed: dict
                      ) -> dict[str, dict[float, dict[str, list[float]]]]:
    """Use only complete, gated EWMA replicate rows; never box aggregate medians."""
    views_by_trial: dict[str, list[tuple[str, float]]] = {}
    for trial in manifest["trials"]:
        views_by_trial[trial["trial_id"]] = [
            (view["name"], float(view["x_value"]))
            for view in trial["views"]
            if view["kind"] == "oat" and view["name"] in observed
        ]
    samples: dict[str, dict[float, dict[str, list[float]]]] = {
        name: {x: {"transfers": [], "relay": []} for x in values}
        for name, values in observed.items()
    }
    seen: set[tuple[str, int]] = set()
    expected_n = int(manifest["total_ctx_per_arm"])
    for row in read_csv(run / "raw_results.csv"):
        if row.get("role") != "ewma_topup":
            continue
        trial_id = row.get("trial_id", "")
        rep = int(row["replicate"])
        key = (trial_id, rep)
        if key in seen:
            raise ValueError(f"Duplicate EWMA replicate: {key}")
        seen.add(key)
        if (row.get("gates_ok", "").lower() != "true"
                or row.get("burn_ok", "").lower() != "true"
                or int(row["n"]) != expected_n):
            continue
        for name, x in views_by_trial.get(trial_id, []):
            if x in samples[name]:
                samples[name][x]["transfers"].append(float(row["transfers"]))
                samples[name][x]["relay"].append(float(row["relayed"]))
    return samples


def draw_reference_style(run: Path, baseline: dict, expected: dict,
                         observed: dict, samples: dict,
                         target_replicates: int) -> list[Path]:
    """Draw true grouped boxes only when every plotted value has repeats."""
    outputs = []
    out = run / "figs" / "reference_style"
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": ["Calibri", "DejaVu Sans"],
        "font.size": 16,
        "font.weight": "bold",
        "axes.labelweight": "bold",
        "pdf.fonttype": 42,
    })
    for name, _, _ in PANELS:
        xs = sorted(expected[name])
        positions = list(range(len(xs)))
        complete_samples = (all(x in observed[name] for x in xs)
                            and all(len(samples[name][x]["relay"]) == target_replicates
                                    and len(samples[name][x]["transfers"]) == target_replicates
                                    for x in xs))
        use_boxes = target_replicates >= 2 and complete_samples
        transfers = [number(observed[name][x].get("target_transfers"))
                     if x in observed[name] else math.nan for x in xs]
        relay = [number(observed[name][x].get("target_relay"))
                 if x in observed[name] else math.nan for x in xs]
        base_index = xs.index(number(baseline[BASELINE_KEYS[name]]))
        red, blue = REFERENCE_COLORS["transfers"], REFERENCE_COLORS["relay"]
        figure_width = 21 if name == "ewma_half_life" else (16 if name == "epsilon" else 14)
        fig, ax = plt.subplots(figsize=(figure_width, 11 if use_boxes else 8.6), dpi=150)
        ax2 = ax.twinx()
        if use_boxes:
            box_style = dict(widths=.34, patch_artist=True, manage_ticks=False,
                             showfliers=True,
                             medianprops={"color": "white", "linewidth": 2.4},
                             whiskerprops={"color": "black", "linewidth": 1.4},
                             capprops={"color": "black", "linewidth": 1.4})
            ax.boxplot([samples[name][x]["transfers"] for x in xs],
                       positions=[p - .19 for p in positions],
                       boxprops={"facecolor": red, "edgecolor": "black", "linewidth": 1.4},
                       **box_style)
            ax2.boxplot([samples[name][x]["relay"] for x in xs],
                        positions=[p + .19 for p in positions],
                        boxprops={"facecolor": blue, "edgecolor": "black", "linewidth": 1.4},
                        **box_style)
        else:
            ax.scatter([p - .12 for p, y in zip(positions, transfers) if math.isfinite(y)],
                       [y for y in transfers if math.isfinite(y)], marker="s", s=28,
                       color=red, edgecolor="black", linewidth=.7, zorder=4)
            ax2.scatter([p + .12 for p, y in zip(positions, relay) if math.isfinite(y)],
                        [y for y in relay if math.isfinite(y)], marker="s", s=28,
                        color=blue, edgecolor="black", linewidth=.7, zorder=5)
        ax.axvline(base_index, color="#777777", lw=1.1,
                   ls=(0, (4, 3)), alpha=.7, zorder=1)
        if name == "epsilon" and 1.0 in xs and 1.0 not in observed[name]:
            ax.axvspan(len(xs) - 1.5, len(xs) - .5,
                       color=red, alpha=.08, zorder=0)
            ax.text(len(xs) - 1, .97, "$\\epsilon$=1.00\\ninvalid",
                    transform=ax.get_xaxis_transform(), color=red,
                    ha="center", va="top", fontsize=11, rotation=90)
        ax.set_xlim(-.6, len(xs) - .4)
        max_transfers = max((v for x in xs for v in samples[name].get(x, {}).get("transfers", [])
                             if math.isfinite(v)), default=max(v for v in transfers if math.isfinite(v)))
        max_relay = max((v for x in xs for v in samples[name].get(x, {}).get("relay", [])
                         if math.isfinite(v)), default=max(v for v in relay if math.isfinite(v)))
        ax.set_ylim(0, max_transfers * 1.17)
        ax2.set_ylim(0, max(1, max_relay * 1.17))
        if max_transfers >= 1000:
            ax.ticklabel_format(axis="y", style="sci", scilimits=(3, 3),
                                useMathText=False)
            ax.yaxis.get_offset_text().set_color(red)
            ax.yaxis.get_offset_text().set_fontweight("bold")
        if name == "epsilon":
            tick_indexes = list(range(0, len(xs), 2))
            if len(xs) - 1 not in tick_indexes:
                tick_indexes.append(len(xs) - 1)
            labels = [f"{xs[i]:.2f}".rstrip("0").rstrip(".")
                      for i in tick_indexes]
        elif name == "ewma_half_life":
            tick_indexes = [i for i, x in enumerate(xs) if x == 1 or x % 5 == 0]
            labels = [f"{xs[i]:g}" for i in tick_indexes]
        else:
            tick_indexes = positions
            labels = [f"{x:g}" for x in xs]
        ax.set_xticks(tick_indexes, labels, fontsize=17, fontweight="bold")
        ax.set_xlabel(REFERENCE_XLABELS[name], fontsize=24, labelpad=12,
                      fontweight="bold")
        ax.set_ylabel("# of rebalancing transfers", color=red,
                      fontsize=24, labelpad=10, fontweight="bold")
        ax2.set_ylabel("# of relay CTXs", color=blue,
                       fontsize=24, labelpad=10, fontweight="bold")
        ax.tick_params(axis="y", colors=red, labelsize=18, width=1.2)
        ax2.tick_params(axis="y", colors=blue, labelsize=18, width=1.2)
        for axis in (ax, ax2):
            for label in axis.get_yticklabels():
                label.set_fontweight("bold")
        ax.grid(axis="both", linestyle=":", color="#888888", alpha=.58)
        ax.set_axisbelow(True)
        note = (f"{target_replicates} independent 10,000-CTX runs per setting; "
                "boxes show across-run distributions."
                if use_boxes else
                "Points show per-setting summaries; incomplete or single-run "
                "settings are not shown as boxplots.")
        fig.text(.5, .023, note, ha="center", fontsize=11,
                 fontweight="normal", color="#444444")
        fig.tight_layout(rect=(0, .065, 1, 1))
        stem = REFERENCE_STEMS[name]
        for suffix in ("png", "pdf"):
            path = out / f"{stem}.{suffix}"
            fig.savefig(path, dpi=300)
            outputs.append(path)
        plt.close(fig)
    return outputs


def draw(run: Path) -> tuple[Path, Path]:
    manifest, baseline, expected, observed = source_data(run)
    samples = replicate_samples(run, manifest, observed)
    target_replicates = int(manifest["replicates"])
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.labelsize": 10, "axes.titlesize": 11,
        "pdf.fonttype": 42, "savefig.dpi": 300,
    })
    fig, axes = plt.subplots(2, 3, figsize=(14.8, 7.4), dpi=140)
    missing: dict[str, list[float]] = {}
    for col, (name, title, xlabel) in enumerate(PANELS):
        xs = sorted(expected[name])
        missing[name] = sorted(set(xs) - set(observed[name]))
        base_x = number(baseline[BASELINE_KEYS[name]])
        for row_index, (field, ylabel, color) in enumerate(METRICS):
            ax = axes[row_index, col]
            ys = [number(observed[name][x].get(field))
                  if x in observed[name] else math.nan for x in xs]
            ax.plot(xs, ys, color=color, lw=1.8, marker="o", ms=3.3,
                    markeredgewidth=0, zorder=3)
            ax.axvline(base_x, color=P.COLOR_GRAY, lw=1.0,
                       ls=(0, (3, 3)), alpha=.85, zorder=1)
            if base_x in observed[name]:
                y0 = number(observed[name][base_x].get(field))
                if math.isfinite(y0):
                    ax.scatter([base_x], [y0], s=65, facecolors="white",
                               edgecolors="black", linewidths=1.2, zorder=5)
            if name == "epsilon" and 1.0 in missing[name]:
                ax.axvspan(.975, 1.025, color=P.COLOR_DATA, alpha=.09,
                           zorder=0)
                ax.set_xlim(.035, 1.035)
                if row_index == 0:
                    ax.text(.03, .97, "$\\epsilon$=1.00 invalid\n"
                            "policy requires $\\epsilon$<1",
                            transform=ax.transAxes, ha="left", va="top",
                            color=P.COLOR_DATA, fontsize=8)
            ax.grid(alpha=.22, lw=.6)
            ax.set_axisbelow(True)
            ax.set_ylim(bottom=0)
            if col == 0:
                ax.set_ylabel(ylabel)
            if row_index == 0:
                ax.set_title(title)
            else:
                ax.set_xlabel(xlabel)

    valid_trials = 0
    for trial in manifest["trials"]:
        status_file = run / "trials" / trial["trial_id"] / "status.json"
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8"))
            valid_trials += bool(status.get("complete"))
    fig.suptitle("EWMA one-factor parameter sensitivity on the cleaned Ethereum trace",
                 fontsize=13, y=.985)
    fig.text(.02, .035,
             f"10,000 CTX per replicate; {target_replicates} route seeds; "
             f"{valid_trials}/{len(manifest['trials'])} "
             "distinct settings complete. Open circles mark the fixed baseline "
             "($\\epsilon$=0.95, sliding window=20, half-life=20; warm-up fixed at 5).",
             fontsize=8.5, color="#444444")
    fig.text(.02, .016,
             "Panels have independent y-axis scales; lines connect per-setting medians. "
             "See reference_style for across-run grouped boxplots when fully replicated.",
             fontsize=8.5, color="#444444")
    fig.tight_layout(rect=(0, .065, 1, .945), w_pad=2.2, h_pad=1.9)

    out = run / "figs"
    out.mkdir(exist_ok=True)
    png, pdf = out / "exp019_paper_sensitivity.png", out / "exp019_paper_sensitivity.pdf"
    fig.savefig(png, dpi=300)
    fig.savefig(pdf)
    plt.close(fig)
    reference_outputs = draw_reference_style(
        run, baseline, expected, observed, samples, target_replicates)
    (out / "exp019_paper_sensitivity_meta.json").write_text(
        json.dumps({
            "source": str(run / "parameter_value_impacts.csv"),
            "manifest": str(run / "manifest.json"),
            "complete_trials": valid_trials,
            "planned_trials": len(manifest["trials"]),
            "observed_values": {name: len(values) for name, values in observed.items()},
            "replicates_planned": target_replicates,
            "replicates_plotted": {
                name: {str(x): len(data["relay"]) for x, data in values.items()}
                for name, values in samples.items()
            },
            "missing_values": missing,
            "reference_style_figures": [str(path) for path in reference_outputs],
            "note": "No missing or failed setting is imputed. Grouped boxes require all planned replicates.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"source: {run / 'parameter_value_impacts.csv'}")
    print(f"complete settings: {valid_trials}/{len(manifest['trials'])}")
    print(f"missing parameter values: {missing}")
    print(f"figures: {png} | {pdf}")
    for path in reference_outputs:
        print(f"reference style: {path}")
    return png, pdf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="existing exp019 output directory")
    args = parser.parse_args()
    draw((args.run or latest_run()).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
