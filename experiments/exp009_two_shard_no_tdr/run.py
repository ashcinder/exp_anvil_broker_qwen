#!/usr/bin/env python3
"""Experiment 9: two-shard balance evolution with configurable TDR."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.procmon import RSSWatchdog
from brokerlab.real_data import extract
from experiments.exp003_tdr_on_off.run import run_arm


def exp_param(cfg, key, kind=int):
    if key not in cfg.exp:
        raise SystemExit(f"config.yaml exp: missing required parameter {key!r}")
    return kind(cfg.exp[key])


def select_directional_workload(cfg):
    count = exp_param(cfg, "count", int)
    pool = extract(
        cfg.traffic.real_csv_path,
        num_shards=cfg.chain.num_shards,
        num_users=cfg.scale.num_users,
        user_base_index=cfg.scale.user_base_index,
        value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
        value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
        limit=exp_param(cfg, "pool_scan", int),
    )
    directions: dict[tuple[int, int], int] = {}
    for row in pool:
        key = (row.src_shard, row.dst_shard)
        directions[key] = directions.get(key, 0) + 1
    if not directions:
        raise SystemExit("no cross-shard rows found in the trace")
    (src, dst), _ = max(directions.items(), key=lambda item: item[1])
    selected = [asdict(r) for r in pool
                if (r.src_shard, r.dst_shard) == (src, dst)][:count]
    if len(selected) < count:
        raise SystemExit(
            f"only {len(selected)} rows available for S{src}->S{dst}; requested {count}"
        )
    for i, row in enumerate(selected):
        row["arrival_pos"] = i
    return selected, src, dst


def build_balance_trace(arm_dir: Path, out_csv: Path, *,
                        num_shards: int, num_brokers: int,
                        initial_eth: float) -> None:
    events: list[tuple[int, int, str, int, int, int]] = []
    with (arm_dir / "ctx_rows.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["ok"].lower() != "true":
                continue
            block = int(row.get("t2_block") or row["submit_block"])
            kind = "broker_ctx" if row["route"] == "broker" else "relay_ctx"
            events.append((block, 1, kind, int(row["src"]),
                           int(row["dst"]), int(row["amount_wei"])))
    move_path = arm_dir / "tdr_move_events.csv"
    if move_path.is_file():
        with move_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                amount = int(row["amount_wei"])
                src, dst = int(row["src_shard"]), int(row["dst_shard"])
                events.append((int(row["burn_block"]), 0, "tdr_burn", src, dst, amount))
                events.append((int(row["mint_block"]), 2, "tdr_mint", src, dst, amount))
    events.sort()

    initial_wei = int(initial_eth * ETH) * num_brokers
    balances = [initial_wei for _ in range(num_shards)]
    grouped: dict[int, list[tuple[int, int, str, int, int, int]]] = {}
    for event in events:
        grouped.setdefault(event[0], []).append(event)
    rows = []
    first_block = min(grouped, default=1)
    rows.append({"block": max(0, first_block - 1),
                 **{f"shard_{s}_balance_wei": balances[s]
                    for s in range(num_shards)},
                 "normal_tx_confirmed": 0, "broker_ctx": 0, "relay_ctx": 0,
                 "tdr_burn": 0, "tdr_mint": 0})
    cumulative_tx = 0
    for block, block_events in sorted(grouped.items()):
        counts = {"broker_ctx": 0, "relay_ctx": 0, "tdr_burn": 0, "tdr_mint": 0}
        for _, _, kind, src, dst, amount in block_events:
            if kind == "broker_ctx":
                balances[src] += amount
                balances[dst] -= amount
                cumulative_tx += 1
            elif kind == "relay_ctx":
                cumulative_tx += 1
            elif kind == "tdr_burn":
                balances[src] -= amount
            else:
                balances[dst] += amount
            counts[kind] += 1
        rows.append({"block": block,
                     **{f"shard_{s}_balance_wei": balances[s]
                        for s in range(num_shards)},
                     "normal_tx_confirmed": cumulative_tx, **counts})
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plot_style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "Calibri", "font.size": 17,
                         "font.weight": "bold", "axes.labelweight": "bold",
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    return plt


def plot_tdr_off(csv_path: Path, out_path: Path) -> None:
    plt = _plot_style()
    from matplotlib.lines import Line2D

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no rows in {csv_path}")
    shard_columns = sorted(
        (name for name in rows[0]
         if name.startswith("shard_") and name.endswith("_balance_wei")),
        key=lambda name: int(name.split("_")[1]),
    )
    txs = [int(r["normal_tx_confirmed"]) for r in rows]
    values = [[int(r[col]) / ETH for r in rows] for col in shard_columns]
    blue = "#28A8F2"
    styles = [(0, (2, 2)), "-"]
    fig, ax = plt.subplots(figsize=(7.75, 7.3), dpi=300)
    fig.subplots_adjust(top=0.90, bottom=0.16, left=0.18, right=0.96)
    handles = []
    for i, (col, series) in enumerate(zip(shard_columns, values)):
        shard = int(col.split("_")[1])
        style = styles[i % len(styles)]
        ax.step(txs, series, where="post", color=blue, ls=style, lw=3.5)
        handles.append(Line2D([0], [0], color=blue, lw=2.5, ls=style,
                              label=f"Broker's balance in shard {shard}"))
    flat = [v for series in values for v in series]
    lo, hi = min(flat), max(flat)
    pad = max((hi - lo) * 0.08, 10.0)
    ax.set_ylim(max(0.0, lo - pad), hi + pad)
    ax.set_xlabel("# of transactions", fontsize=30, weight="bold")
    ax.set_ylabel("Broker α's balance (ETH)", color=blue, fontsize=28, weight="bold")
    ax.tick_params(axis="both", labelsize=22)
    ax.grid(linestyle=":", alpha=0.6)
    ax.legend(handles=handles, loc="upper left", frameon=True,
              edgecolor="#888888", fontsize=16)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def plot_tdr_on(snapshot_path: Path, out_path: Path, target_shard: int) -> None:
    plt = _plot_style()
    from matplotlib.lines import Line2D

    with snapshot_path.open("r", encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    rows = [r for r in all_rows if int(r["broker_idx"]) == 0]
    if not rows:
        raise ValueError(f"no broker-0 snapshots in {snapshot_path}")
    bal_col = f"shard_{target_shard}_balance"
    tau_col = f"shard_{target_shard}_tau"
    upper_col = f"shard_{target_shard}_upper_bound"
    rows = [r for r in rows if r.get(bal_col) not in (None, "")]
    txs = [int(r["normal_tx_confirmed"]) for r in rows]
    balances = [int(r[bal_col]) / ETH for r in rows]
    tau = [int(r[tau_col]) / ETH if r.get(tau_col) not in (None, "") else float("nan")
           for r in rows]
    upper = [int(r[upper_col]) / ETH if r.get(upper_col) not in (None, "") else float("nan")
             for r in rows]
    confirmed = [int(r["tdr_self_transfer_confirmed"]) for r in rows]
    event_indices = [i for i, value in enumerate(confirmed)
                     if value > (confirmed[i - 1] if i else 0)]

    blue, orange, red, gray = "#28A8F2", "#FFA537", "#DE2B03", "#888888"
    fig, ax = plt.subplots(figsize=(10.0, 8.0), dpi=300)
    fig.subplots_adjust(left=0.15, right=0.98, bottom=0.12, top=0.98)
    ax.plot(txs, upper, color=gray, ls="--", lw=2.0)
    ax.plot(txs, balances, color=blue, lw=3.5)
    ax.plot(txs, tau, color=orange, lw=4.5)
    ax.scatter([txs[i] for i in event_indices], [balances[i] for i in event_indices],
               color=red, marker="o", s=110, edgecolors="white", linewidths=0.8,
               zorder=7)
    flat = [v for v in balances + tau + upper if v == v]
    lo, hi = min(flat), max(flat)
    pad = max((hi - lo) * 0.08, 1.0)
    ax.set_ylim(max(0.0, lo - pad), hi + pad)
    ax.set_xlabel("# of transactions", fontsize=34, weight="bold")
    ax.set_ylabel(f"Broker α's balance in shard {target_shard} (ETH)",
                  fontsize=32, weight="bold", labelpad=15)
    ax.tick_params(axis="both", labelsize=27)
    ax.grid(linestyle=":", alpha=0.6)
    ax.legend(handles=[
        Line2D([0], [0], color=gray, ls="--", lw=2.0,
               label=f"α.{target_shard} upper bound balance"),
        Line2D([0], [0], color=blue, lw=3.5,
               label=f"α.{target_shard}'s current balance"),
        Line2D([0], [0], color=orange, lw=4.5,
               label=f"α.{target_shard}'s target balance"),
        Line2D([0], [0], color=red, marker="o", linestyle="None", markersize=10,
               markeredgecolor="white", label="# of confirmed TDR self-transfers"),
    ], loc="upper left", frameon=True, edgecolor=gray, fontsize=20)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "config.yaml"))
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    parser.add_argument("--plot-only", type=Path,
                        help="redraw balance_trace.csv or balance_snapshots.csv")
    parser.add_argument("--target-shard", type=int, default=0)
    args = parser.parse_args()
    if args.plot_only:
        is_snapshot = args.plot_only.name == "balance_snapshots.csv"
        stem = "fig_tdr_on" if is_snapshot else "fig_tdr_off"
        out_path = args.plot_only.parent / "figs" / f"{stem}.png"
        if is_snapshot:
            plot_tdr_on(args.plot_only, out_path, args.target_shard)
        else:
            plot_tdr_off(args.plot_only, out_path)
        print(f"figure: {out_path}")
        return 0

    cfg = apply_overrides(load_config(args.config), args.overrides)
    if cfg.chain.num_shards != 2:
        raise SystemExit("exp009 requires chain.num_shards=2")
    selected, src, dst = select_directional_workload(cfg)
    total_wei = sum(int(row["amount_wei"]) for row in selected)
    enabled = bool(cfg.tdr.enabled)
    balance = exp_param(cfg, "balances_eth", float)
    arm = f"{'tdr_on' if enabled else 'tdr_off'}@{balance:g}"
    engine = "proportional" if enabled else "plain"
    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True)

    watchdog = RSSWatchdog(exp_param(cfg, "rss_cap_mb", float))
    watchdog.start()
    try:
        stats = run_arm(cfg, arm, selected, total_wei, out_root, watchdog,
                        engine=engine, fund_eth=balance)
    finally:
        watchdog.stop()

    arm_dir = out_root / arm
    trace_csv = out_root / "balance_trace.csv"
    build_balance_trace(arm_dir, trace_csv, num_shards=2,
                        num_brokers=cfg.scale.num_brokers,
                        initial_eth=balance)
    if enabled:
        plot_source = arm_dir / "balance_snapshots.csv"
        fig_path = out_root / "figs" / "fig_tdr_on.png"
        plot_tdr_on(plot_source, fig_path, dst)
    else:
        plot_source = trace_csv
        fig_path = out_root / "figs" / "fig_tdr_off.png"
        plot_tdr_off(plot_source, fig_path)
    summary = {
        "params": to_params_dict(cfg, extra={"direction": f"S{src}->S{dst}"}),
        "tdr_enabled": enabled, "arm": arm, "stats": stats,
        "balance_trace_csv": str(trace_csv), "plot_source": str(plot_source),
        "figure": str(fig_path),
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"TDR: {'ON' if enabled else 'OFF'}")
    print(f"balance CSV: {trace_csv}")
    print(f"figure: {fig_path}")
    ok = (not stats["failed"] and all(stats["gates"].values())
          and stats["burn_reconcile"]["ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
