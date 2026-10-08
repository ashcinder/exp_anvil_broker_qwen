#!/usr/bin/env python3
"""Create an epsilon boxplot from completed exp023 replicate data."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
from matplotlib.patches import Patch

VALUES = [i / 100 for i in range(5, 96, 5)]


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def plot(run: Path):
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    expected_n = int(manifest["total_ctx_per_arm"])
    rows = read_csv(run / "raw_results.csv")
    by_trial = {}
    for row in rows:
        by_trial.setdefault(row.get("trial_id"), []).append(row)
    groups = {value: {"transfers": [], "relay": []} for value in VALUES}

    for trial in manifest["trials"]:
        values = [float(view["x_value"]) for view in trial.get("views", [])
                  if view.get("kind") == "oat" and view.get("name") == "epsilon"]
        if values:
            epsilon = values[0]
        elif any(view.get("kind") == "baseline" for view in trial.get("views", [])):
            # The configured baseline may share a trial with an OAT point.
            epsilon = float(trial["state"]["ewma_epsilon"])
        else:
            continue
        if epsilon not in groups:
            continue
        for row in by_trial.get(trial["trial_id"], []):
            if row.get("role") != "ewma_topup":
                continue
            if row.get("gates_ok", "").lower() != "true" or row.get("burn_ok", "").lower() != "true":
                continue
            if int(float(row.get("n", 0))) != expected_n:
                continue
            groups[epsilon]["transfers"].append(float(row["transfers"]))
            groups[epsilon]["relay"].append(float(row["relayed"]))

    replicates = int(manifest["replicates"])
    bad = {value: len(data["transfers"]) for value, data in groups.items()
           if len(data["transfers"]) != replicates}
    if bad:
        raise SystemExit(f"Expected {replicates} valid EWMA replicates per epsilon; got {bad}")

    out = run / "figs" / "reference_style"
    out.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(VALUES), dtype=float)
    fig, left = plt.subplots(figsize=(12.2, 5.8), constrained_layout=True)
    right = left.twinx()
    left.boxplot([groups[v]["transfers"] for v in VALUES], positions=x - 0.17,
                 widths=0.28, patch_artist=True, showfliers=True,
                 boxprops={"facecolor": "#DE2B03", "alpha": 0.35, "edgecolor": "#B52200"},
                 medianprops={"color": "#B52200", "linewidth": 1.5},
                 whiskerprops={"color": "#B52200"}, capprops={"color": "#B52200"},
                 flierprops={"marker": ".", "markersize": 3, "markerfacecolor": "#B52200",
                             "markeredgecolor": "none", "alpha": 0.5})
    right.boxplot([groups[v]["relay"] for v in VALUES], positions=x + 0.17,
                  widths=0.28, patch_artist=True, showfliers=True,
                  boxprops={"facecolor": "#28A8F2", "alpha": 0.35, "edgecolor": "#087DBB"},
                  medianprops={"color": "#087DBB", "linewidth": 1.5},
                  whiskerprops={"color": "#087DBB"}, capprops={"color": "#087DBB"},
                  flierprops={"marker": ".", "markersize": 3, "markerfacecolor": "#087DBB",
                              "markeredgecolor": "none", "alpha": 0.5})
    baseline_trial = next(
        trial for trial in manifest["trials"]
        if any(view.get("kind") == "baseline" for view in trial.get("views", []))
    )
    baseline = float(baseline_trial["state"]["ewma_epsilon"])
    if baseline in VALUES:
        left.axvline(VALUES.index(baseline), color="#555555", linestyle="--", linewidth=1)
    left.set_xticks(x, [f"{value:.2f}" for value in VALUES], rotation=45, ha="right")
    left.set_xlabel("EWMA deficit threshold ($\\epsilon$)")
    left.set_ylabel("Completed TDR transfers", color="#B52200")
    right.set_ylabel("Relay fallbacks (CTX)", color="#087DBB")
    left.grid(axis="y", color="#dddddd", linewidth=0.7)
    left.set_axisbelow(True)
    left.spines["top"].set_visible(False)
    right.spines["top"].set_visible(False)
    left.set_title("EWMA epsilon sensitivity")
    left.legend(handles=[Patch(facecolor="#DE2B03", alpha=0.35, label="TDR transfers"),
                         Patch(facecolor="#28A8F2", alpha=0.35, label="Relay fallbacks")],
                loc="upper left", frameon=False)

    stem = out / "fig6a_epsilon_exp023"
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {stem.with_suffix('.pdf')} and {stem.with_suffix('.png')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    plot(parser.parse_args().run.resolve())


if __name__ == "__main__":
    main()
