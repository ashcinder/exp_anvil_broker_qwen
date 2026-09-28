#!/usr/bin/env python3
"""Render exp010 OAT curves, grid heatmaps, Pareto plot and leaderboard."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROLE_ORDER = ["plain", "valve", "proportional", "hard_topup", "ewma_topup"]
COLORS = {"plain": "#606060", "valve": "#E69F00", "proportional": "#CC79A7",
          "hard_topup": "#56B4E9", "ewma_topup": "#009E73"}
LABELS = {"plain": "Plain", "valve": "Valve", "proportional": "Proportional",
          "hard_topup": "Hard-window topup", "ewma_topup": "EWMA topup"}
VALUE_PREFIX = {"plain": "plain", "valve": "valve", "proportional": "proportional",
                "hard_topup": "hard", "ewma_topup": "ewma"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def setup():
    plt.rcParams.update({"font.size": 9, "axes.titlesize": 11,
                         "axes.labelsize": 9, "legend.fontsize": 8,
                         "figure.dpi": 140, "savefig.dpi": 180})


def save(fig, path: Path, rect=None):
    fig.tight_layout(rect=rect)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_oat(rows: list[dict[str, str]], out: Path) -> list[str]:
    made = []
    groups = defaultdict(list)
    for row in rows:
        if row.get("kind") == "oat":
            groups[row["name"]].append(row)
    oat_dir = out / "oat"
    oat_dir.mkdir(exist_ok=True)
    metrics = [("relayed_median", "Relay count"),
               ("events_median", "TDR trigger events"),
               ("transfers_median", "TDR transfers"),
               ("relay_plus_transfers_median", "Relay + TDR transfers"),
               ("moved_eth_median", "Moved liquidity (ETH)"),
               ("full_wall_throughput_ctx_per_s_median", "Full-wall throughput (ctx/s)")]
    for name, items in groups.items():
        fig, axes = plt.subplots(2, 3, figsize=(14, 7.2))
        for ax, (metric, ylabel) in zip(axes.flat, metrics):
            for role in ROLE_ORDER:
                rs = [r for r in items if r["role"] == role]
                rs.sort(key=lambda r: num(r["x_value"]))
                if not rs:
                    continue
                xs, ys = [num(r["x_value"]) for r in rs], [num(r.get(metric)) for r in rs]
                ax.plot(xs, ys, marker="o", ms=3.5, lw=1.4,
                        color=COLORS[role], label=LABELS[role])
            ax.set_xlabel(items[0].get("label") or items[0].get("x_param"))
            ax.set_ylabel(ylabel)
            ax.grid(alpha=.22, lw=.6)
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5, .955),
                   ncol=min(5, len(labels)), frameon=False)
        fig.suptitle(f"One-at-a-time sensitivity: {name}", y=.995)
        path = oat_dir / f"{name}.png"
        save(fig, path, rect=(0, 0, 1, .90)); made.append(str(path))
    return made


def heatmap(ax, grid_rows, metric, title):
    xs = sorted({num(r["x_value"]) for r in grid_rows})
    ys = sorted({num(r["y_value"]) for r in grid_rows})
    matrix = np.full((len(ys), len(xs)), np.nan)
    lookup = {(num(r["x_value"]), num(r["y_value"])): num(r.get(metric))
              for r in grid_rows}
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            matrix[yi, xi] = lookup.get((x, y), np.nan)
    im = ax.imshow(matrix, origin="lower", aspect="auto", cmap="viridis")
    ax.set_xticks(range(len(xs)), [f"{x:g}" for x in xs], rotation=35, ha="right")
    ax.set_yticks(range(len(ys)), [f"{y:g}" for y in ys])
    ax.set_xlabel(grid_rows[0].get("x_label") or grid_rows[0]["x_param"])
    ax.set_ylabel(grid_rows[0].get("y_label") or grid_rows[0]["y_param"])
    ax.set_title(title)
    plt.colorbar(im, ax=ax, fraction=.046, pad=.04)


def plot_grids(aggregate: list[dict[str, str]], comparisons: list[dict[str, str]],
               out: Path) -> list[str]:
    made, agg_groups, cmp_groups = [], defaultdict(list), defaultdict(list)
    for row in aggregate:
        if row.get("kind") == "grid" and row.get("role") == "ewma_topup":
            agg_groups[row["name"]].append(row)
    for row in comparisons:
        if row.get("kind") == "grid":
            cmp_groups[row["name"]].append(row)
    grid_dir = out / "grids"; grid_dir.mkdir(exist_ok=True)
    for name, items in agg_groups.items():
        fig, axes = plt.subplots(1, 4, figsize=(18, 4.2))
        heatmap(axes[0], items, "relayed_median", "EWMA relay")
        heatmap(axes[1], items, "events_median", "EWMA TDR events")
        heatmap(axes[2], items, "relay_plus_transfers_median",
                "EWMA relay + TDR transfers")
        heatmap(axes[3], cmp_groups.get(name, []), "ewma_minus_valve_relay",
                "EWMA relay minus valve")
        fig.suptitle(f"Pairwise grid: {name}")
        path = grid_dir / f"{name}.png"
        save(fig, path, rect=(0, 0, 1, .95)); made.append(str(path))
    return made


def plot_pareto(rows, out: Path) -> str | None:
    rs = [r for r in rows if r.get("kind") != "baseline"
          and math.isfinite(num(r.get("relayed_median")))
          and math.isfinite(num(r.get("transfers_median")))]
    if not rs: return None
    fig, ax = plt.subplots(figsize=(9.5, 6.2))
    for role in ROLE_ORDER:
        part = [r for r in rs if r["role"] == role]
        if not part: continue
        ax.scatter([num(r["transfers_median"]) for r in part],
                   [num(r["relayed_median"]) for r in part], s=18, alpha=.55,
                   color=COLORS[role], label=LABELS[role])
    ax.set_xlabel("Rebalancing transfers (median)")
    ax.set_ylabel("Relay count (median)")
    ax.set_title("Relay–rebalancing trade-off across search trials")
    ax.grid(alpha=.22); ax.legend(frameon=False)
    path = out / "pareto_relay_vs_transfers.png"; save(fig, path); return str(path)


def plot_leaderboard(rows, out: Path) -> str | None:
    rs = [r for r in rows if r.get("ewma_beats_valve", "").lower() == "true"
          and r.get("informative_load", "").lower() == "true"
          and math.isfinite(num(r.get("ewma_relay")))]
    rs.sort(key=lambda r: (num(r["ewma_relay"]), num(r.get("ewma_transfers"))))
    rs = rs[:20]
    if not rs: return None
    labels = [f"{r.get('name')} · {r.get('x_value')}" for r in rs][::-1]
    vals = [num(r["ewma_relay"]) for r in rs][::-1]
    fig, ax = plt.subplots(figsize=(10, max(4.5, .32 * len(rs))))
    ax.barh(labels, vals, color=COLORS["ewma_topup"])
    ax.set_xlabel("EWMA relay count (median)")
    ax.set_title("Top EWMA trials that beat valve")
    ax.grid(axis="x", alpha=.22)
    path = out / "leaderboard_ewma.png"; save(fig, path); return str(path)


def plot_parameter_importance(rows, out: Path) -> str | None:
    rows = [r for r in rows if math.isfinite(num(r.get("overall_effect_rate")))]
    rows.sort(key=lambda r: num(r["overall_effect_rate"]))
    if not rows:
        return None
    labels = [r.get("parameter_label") or r["parameter"] for r in rows]
    relay = [100 * num(r.get("relay_rate_span")) for r in rows]
    rebal = [100 * num(r.get("rebalancing_rate_span")) for r in rows]
    y = np.arange(len(rows)); height = .38
    fig, ax = plt.subplots(figsize=(11, max(5, .55 * len(rows))))
    ax.barh(y - height / 2, relay, height, label="Relay-rate span",
            color="#0072B2")
    ax.barh(y + height / 2, rebal, height, label="Rebalancing/CTX span",
            color="#D55E00")
    ax.set_yticks(y, labels)
    ax.set_xlabel("Observed span across tested values (percentage points)")
    ax.set_title("Parameter influence on routing outcome and rebalancing cost")
    ax.grid(axis="x", alpha=.22); ax.legend(frameon=False)
    path = out / "parameter_importance.png"; save(fig, path); return str(path)


def plot_parameter_values(rows, out: Path) -> list[str]:
    made, groups = [], defaultdict(list)
    for row in rows:
        groups[row["parameter"]].append(row)
    value_dir = out / "parameter_values"; value_dir.mkdir(exist_ok=True)
    for name, items in groups.items():
        items.sort(key=lambda r: num(r.get("value")))
        xs = [num(r.get("value")) for r in items]
        fig, axes = plt.subplots(2, 3, figsize=(15, 7.5))
        for role in ROLE_ORDER:
            prefix = VALUE_PREFIX[role]
            color, label = COLORS[role], LABELS[role]
            relay = [100 * num(r.get(f"{prefix}_relay_rate")) for r in items]
            events = [num(r.get(f"{prefix}_events")) for r in items]
            transfers = [num(r.get(f"{prefix}_transfers")) for r in items]
            combined = [num(r.get(f"{prefix}_relay_plus_transfers")) for r in items]
            moved = [num(r.get(f"{prefix}_moved_eth")) for r in items]
            if any(math.isfinite(v) for v in relay):
                axes[0, 0].plot(xs, relay, marker="o", ms=3.5, color=color,
                                label=label)
            if any(math.isfinite(v) for v in events):
                axes[0, 1].plot(xs, events, marker="o", ms=3.5, color=color,
                                label=label)
            if any(math.isfinite(v) for v in transfers):
                axes[0, 2].plot(xs, transfers, marker="o", ms=3.5, color=color,
                                label=label)
            if any(math.isfinite(v) for v in combined):
                axes[1, 0].plot(xs, combined, marker="o", ms=3.5, color=color,
                                label=label)
            if any(math.isfinite(v) for v in moved):
                axes[1, 1].plot(xs, moved, marker="o", ms=3.5, color=color,
                                label=label)
        for baseline, color in (("plain", "#606060"), ("valve", "#E69F00")):
            ys = [100 * num(r.get(f"ewma_relay_rate_improvement_vs_{baseline}"))
                  for r in items]
            if any(math.isfinite(v) for v in ys):
                axes[1, 2].plot(xs, ys, marker="o", ms=4, color=color,
                                label=f"EWMA vs {baseline.title()}")
        axes[1, 2].axhline(0, color="#222222", lw=.8, alpha=.6)
        axes[0, 0].set_ylabel("Relay rate (%)")
        axes[0, 1].set_ylabel("TDR trigger events")
        axes[0, 2].set_ylabel("TDR transfers")
        axes[1, 0].set_ylabel("Relay + TDR transfers")
        axes[1, 1].set_ylabel("Moved liquidity (ETH)")
        axes[1, 2].set_ylabel("EWMA relay improvement (percentage points)")
        xlabel = items[0].get("parameter_label") or name
        for ax in axes.flat:
            ax.set_xlabel(xlabel); ax.grid(alpha=.22, lw=.6)
        axes[0, 0].legend(frameon=False, fontsize=7)
        axes[1, 2].legend(frameon=False, fontsize=7)
        fig.suptitle(f"Observed effect by parameter value: {name}")
        path = value_dir / f"{name}.png"
        save(fig, path, rect=(0, 0, 1, .96)); made.append(str(path))
    return made


def plot_five_way_baseline(rows: list[dict[str, str]], out: Path) -> str | None:
    baseline = next((row for row in rows
                     if any(view.get("kind") == "baseline"
                            for view in json.loads(row.get("views_json") or "[]"))), None)
    if baseline is None:
        return None
    metrics = (("relay", "Relay"), ("events", "TDR trigger events"),
               ("transfers", "TDR transfers"),
               ("relay_plus_transfers", "Relay + TDR transfers"))
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    for ax, (key, title) in zip(axes.flat, metrics):
        values = [num(baseline.get(f"{role}_{key}")) for role in ROLE_ORDER]
        ax.bar(range(len(ROLE_ORDER)), values,
               color=[COLORS[role] for role in ROLE_ORDER])
        ax.set_xticks(range(len(ROLE_ORDER)),
                      [LABELS[role] for role in ROLE_ORDER], rotation=25,
                      ha="right")
        ax.set_title(title)
        ax.grid(axis="y", alpha=.22)
    fig.suptitle("Five-policy comparison at the fixed pressure baseline")
    path = out / "five_way_baseline.png"
    save(fig, path)
    return str(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    args = ap.parse_args(); setup()
    aggregate_path, comparisons_path = args.run / "aggregate.csv", args.run / "comparisons.csv"
    if not aggregate_path.exists():
        raise SystemExit(f"missing {aggregate_path}")
    rows = read_csv(aggregate_path)
    comparisons = read_csv(comparisons_path) if comparisons_path.exists() else []
    effects = read_csv(args.run / "parameter_effects.csv") \
        if (args.run / "parameter_effects.csv").exists() else []
    values = read_csv(args.run / "parameter_value_impacts.csv") \
        if (args.run / "parameter_value_impacts.csv").exists() else []
    five_way = read_csv(args.run / "five_way_comparisons.csv") \
        if (args.run / "five_way_comparisons.csv").exists() else []
    figs = args.run / "figs"; figs.mkdir(exist_ok=True)
    made = plot_oat(rows, figs)
    made += plot_grids(rows, comparisons, figs)
    made += plot_parameter_values(values, figs)
    for item in (plot_pareto(rows, figs), plot_leaderboard(comparisons, figs),
                 plot_parameter_importance(effects, figs),
                 plot_five_way_baseline(five_way, figs)):
        if item: made.append(item)
    (figs / "index.json").write_text(json.dumps({"figures": made}, indent=2),
                                      encoding="utf-8")
    print(f"rendered {len(made)} figures under {figs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
