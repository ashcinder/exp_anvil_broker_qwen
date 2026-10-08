#!/usr/bin/env python3
"""Revalue broker 19 in a completed 40k exp008 run; no experiment is run."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MultipleLocator, ScalarFormatter

HERE = Path(__file__).resolve().parent
RUN = HERE / "out" / "20260924_084115"
BROKER = 19
GAS_WEI = 21_000_000
FEE_SHARE = 0.10
FEE_INCOME_WEI = GAS_WEI * FEE_SHARE
PLAIN = "plain@150.0"
TDR = "topup@150.0@0.95@20.0"


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    report = json.loads((RUN / "report.json").read_text(encoding="utf-8"))
    if report.get("verdicts", {}).get("V1_gates_every_session") is not True:
        raise ValueError("Source exp008 run failed integrity gates")
    session_files = list((RUN / "session_1").glob("*/summary.json"))
    if len(session_files) != 1:
        raise ValueError("Expected one completed exp008 session")
    session = session_files[0].parent
    summary = json.loads(session_files[0].read_text(encoding="utf-8"))
    if not summary.get("passed"):
        raise ValueError("Source session did not pass")
    arm_data = {}
    for arm in (PLAIN, TDR):
        arm_summary = next(a for a in summary["arms"] if a["arm"] == arm)
        if not (arm_summary["gates"] and all(arm_summary["gates"].values())
                and arm_summary["burn_reconcile"]["ok"]):
            raise ValueError(f"Integrity checks failed for {arm}")
        tx_rows = rows(session / arm / "ctx_rows.csv")
        if len(tx_rows) != 40_000 or any(r["ok"].lower() != "true" for r in tx_rows):
            raise ValueError(f"Unexpected CTX count or unsuccessful CTX in {arm}")
        tx_rows.sort(key=lambda r: int(r["arrival_pos"]))
        if [int(r["arrival_pos"]) for r in tx_rows] != list(range(40_000)):
            raise ValueError(f"Invalid arrival positions in {arm}")
        arm_data[arm] = (tx_rows, arm_summary)

    p_rows, p_summary = arm_data[PLAIN]
    t_rows, t_summary = arm_data[TDR]
    if any(p["ctx_id"] != t["ctx_id"] for p, t in zip(p_rows, t_rows)):
        raise ValueError("Plain and TDR do not contain the same ordered workload")
    # Keep the first insufficient-balance fallback as a diagnostic in metadata.
    # The plot marker below instead identifies the point where Plain's final
    # broker service occurs and its cumulative profit becomes flat.
    first_shortfall = next(
        (r for r in p_rows if int(r["broker_idx"]) == BROKER and r["route"] == "relay"),
        None,
    )
    if first_shortfall is None:
        raise ValueError(f"Broker {BROKER} has no observed Plain balance shortfall")
    first_shortfall_pos = int(first_shortfall["arrival_pos"]) + 1

    cumulative = {PLAIN: [], TDR: []}
    for arm, txs in ((PLAIN, p_rows), (TDR, t_rows)):
        served = 0
        for pos, row in enumerate(txs, 1):
            if row["route"] == "broker" and int(row["broker_idx"]) == BROKER:
                served += 1
            cumulative[arm].append({"transactions": pos, "served": served,
                                    "gross_fee_wei": served * FEE_INCOME_WEI})

    plain_service_positions = [r["transactions"] for r in cumulative[PLAIN]
                               if r["served"] > 0 and
                               (r["transactions"] == 1 or
                                r["served"] > cumulative[PLAIN][r["transactions"] - 2]["served"])]
    if not plain_service_positions:
        raise ValueError("Broker 19 was never served by Plain")
    last_plain_service = plain_service_positions[-1]
    plain_total = cumulative[PLAIN][-1]["served"]
    tdr_total = cumulative[TDR][-1]["served"]
    if any(r["served"] != plain_total for r in cumulative[PLAIN]
           if r["transactions"] >= last_plain_service):
        raise ValueError("Plain does not remain at a service plateau")
    tdr_tail_service = tdr_total - cumulative[TDR][last_plain_service - 1]["served"]
    if tdr_tail_service <= 0:
        raise ValueError("TDR broker has no service after the Plain plateau")

    moves = rows(session / TDR / "tdr_moves.csv")
    broker_moves = sum(int(m["broker_idx"]) == BROKER and m["state"] == "done"
                       for m in moves)
    if t_summary["tdr"]["transfers_lost"] != 0:
        raise ValueError("Source TDR arm includes a lost transfer")

    # exp008 has B2E disabled. Per the requested estimate, the service Θ2 gas
    # sets the fee-income basis; only broker-signed TDR burns count as costs.
    # Coordinator mints and user-signed legs are excluded.
    totals = {}
    for name, count, tdr_count in (("plain", plain_total, 0),
                                   ("tdr", tdr_total, broker_moves)):
        revenue = round(count * FEE_INCOME_WEI)
        tdr_gas = tdr_count * GAS_WEI
        totals[name] = {"broker_served": count, "broker_tdr_burns": tdr_count,
                        "gross_fee_income_wei": revenue,
                        "tdr_gas_cost_wei": tdr_gas,
                        "net_profit_wei": revenue - tdr_gas}

    # Service revenue is 10% of the fixed gas fee per broker service. Under the
    # requested accounting, only broker-paid TDR rebalance gas is a cost. The
    # legacy move log lacks event positions, so spread TDR burn gas evenly over
    # arrivals; this preserves exact terminal profit but estimates its timing.
    tdr_burn_gas_total = broker_moves * GAS_WEI
    for arm, txs in ((PLAIN, p_rows), (TDR, t_rows)):
        served = 0
        for pos, row in enumerate(txs, 1):
            if row["route"] == "broker" and int(row["broker_idx"]) == BROKER:
                served += 1
            burn_cost = round(tdr_burn_gas_total * pos / 40_000) if arm == TDR else 0
            cumulative[arm][pos - 1]["estimated_tdr_gas_wei"] = burn_cost
            cumulative[arm][pos - 1]["net_profit_wei"] = (
                served * FEE_INCOME_WEI - burn_cost
            )
        if cumulative[arm][-1]["net_profit_wei"] != totals["tdr" if arm == TDR else "plain"]["net_profit_wei"]:
            raise ValueError(f"Cumulative profit does not reconcile for {arm}")

    output = HERE / "figs"
    output.mkdir(exist_ok=True)
    stem = "exp008_existing_broker19_profit"
    with (output / f"{stem}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["arm", "transactions", "broker_served", "fee_income_wei",
                         "estimated_tdr_gas_cost_wei",
                         "cumulative_net_profit_wei"])
        for arm in (PLAIN, TDR):
            for row in cumulative[arm]:
                writer.writerow(["plain" if arm == PLAIN else "tdr",
                                 row["transactions"], row["served"], row["gross_fee_wei"],
                                 row["estimated_tdr_gas_wei"], row["net_profit_wei"]])

    from matplotlib.lines import Line2D
    fig, curve = plt.subplots(figsize=(10.5, 7.3), dpi=180)
    line_handles = []
    for arm, name, color, label in (
        (TDR, "tdr", "#df320b", "With TDR (Topup + EWMA)"),
        (PLAIN, "plain", "#28a5ed", "Without TDR (Plain)"),
    ):
        data = cumulative[arm]
        marker = "^" if arm == TDR else "s"
        marker_size = 7.2 if arm == TDR else 6.2
        line, = curve.step([0] + [r["transactions"] for r in data],
                           [0] + [r["net_profit_wei"] / 1e18 for r in data],
                           where="post", color=color, lw=2.1, label=label,
                           marker=marker, markevery=1000, markersize=marker_size,
                           markerfacecolor="white", markeredgewidth=1.35)
        line_handles.append(line)
    curve.set(xlim=(0, 40_000), xlabel="Transactions in shared arrival order",
              ylabel="Broker total profit (ETH)")
    curve.set_ylim(-0.1e-9, 1.45e-9)
    # Keep ETH as the plotted unit, but let ScalarFormatter show one shared
    # 1e-9 scale factor instead of repeating scientific notation on every tick.
    curve.yaxis.set_major_locator(MultipleLocator(0.2e-9))
    curve_formatter = ScalarFormatter(useMathText=False)
    curve_formatter.set_scientific(True)
    curve_formatter.set_powerlimits((-9, -9))
    curve.yaxis.set_major_formatter(curve_formatter)
    curve.grid(alpha=0.25)
    curve.set_xlabel("Transactions in shared arrival order", color="black",
                     fontweight="bold")
    curve.set_ylabel("Broker total profit (ETH)", color="#c62828",
                     fontweight="bold")
    curve.tick_params(axis="x", colors="black", width=1.4)
    curve.tick_params(axis="y", color="black", labelcolor="#c62828", width=1.4)
    curve.spines["bottom"].set_color("black")
    curve.spines["bottom"].set_linewidth(1.4)
    curve.spines["left"].set_color("black")
    curve.spines["left"].set_linewidth(1.4)
    for tick in curve.get_xticklabels():
        tick.set_fontweight("bold")
    for tick in curve.get_yticklabels():
        tick.set_fontweight("bold")

    stop_profit = cumulative[PLAIN][last_plain_service - 1]["net_profit_wei"] / 1e18
    stop_handle = Line2D(
        [0], [0], marker="s", linestyle="none", markerfacecolor="#28a5ed",
        markeredgecolor="#111111", markersize=7,
        label=f"Plain stops serving: CTX {last_plain_service:,}",
    )
    curve.scatter([last_plain_service], [stop_profit], marker="s", s=82,
                  facecolor="#28a5ed", edgecolor="#111111", linewidth=1.3,
                  zorder=7)
    from matplotlib.patches import ConnectionPatch, Rectangle
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes
    # Shift the inset right of the main y-axis labels while leaving the
    # post-20k portion of the main trajectory visible.
    zoom = inset_axes(
        curve, width="43%", height="40%", loc="upper left",
        bbox_to_anchor=(0.08, 0.0, 0.92, 1.0),
        bbox_transform=curve.transAxes, borderpad=1.2,
    )
    zoom.set_facecolor("white")
    zoom.patch.set_alpha(0.97)
    zoom_start = max(0, last_plain_service - 500)
    for arm, color, label, marker in ((TDR, "#df320b", "With TDR", "^"),
                                      (PLAIN, "#28a5ed", "Without TDR", "s")):
        data = cumulative[arm]
        xs = [r["transactions"] for r in data if r["transactions"] >= zoom_start]
        ys = [r["net_profit_wei"] / 1e18 for r in data
              if r["transactions"] >= zoom_start]
        zoom.plot(xs, ys, color=color, lw=2.1, label=label, marker=marker,
                  markevery=250, markersize=6.2 if arm == TDR else 5.4,
                  markerfacecolor="white", markeredgewidth=1.3)
    zoom.scatter([last_plain_service], [stop_profit], marker="s", s=60,
                 facecolor="#28a5ed", edgecolor="#111111", linewidth=1.2,
                 zorder=7)
    zoom.annotate(
        "Broker balance exhausted",
        xy=(last_plain_service, stop_profit), xytext=(24, 20),
        textcoords="offset points", ha="left", va="bottom",
        fontsize=7, fontweight="bold", color="#222222",
        arrowprops={"arrowstyle": "->", "color": "#111111", "linewidth": 1.5},
        bbox={"facecolor": "white", "edgecolor": "#777777", "alpha": 0.94,
              "boxstyle": "round,pad=0.25"},
        zorder=8,
    )
    zoom.set(xlim=(zoom_start, 40_000),
             xticks=[zoom_start, 38_000, 39_000, 40_000])
    zvals = [r["net_profit_wei"] / 1e18 for arm in (PLAIN, TDR)
             for r in cumulative[arm][zoom_start - 1:]]
    pad = max((max(zvals) - min(zvals)) * 0.12, 1e-12)
    zoom.set_ylim(min(zvals) - pad, max(zvals) + pad)
    zoom.yaxis.set_major_locator(MultipleLocator(0.1e-9))
    # The main axis carries the shared 1e-9 scale label; show inset tick values
    # in that same scale without repeating the exponent beside its title.
    zoom.yaxis.set_major_formatter(
        FuncFormatter(lambda value, _pos: f"{value / 1e-9:.1f}")
    )
    zoom.tick_params(axis="both", labelsize=7)
    zoom.tick_params(axis="y", labelleft=True, labelright=False)
    zoom.grid(alpha=0.25)
    zoom.set_title(f"TDR OFF: balance exhausted at CTX {last_plain_service:,}", fontsize=8,
                   fontweight="bold", pad=4)
    zoom.tick_params(axis="x", colors="black", width=1.2)
    zoom.tick_params(axis="y", color="black", labelcolor="#c62828", width=1.2)
    zoom.spines["bottom"].set_color("black")
    zoom.spines["left"].set_color("black")
    for tick in zoom.get_xticklabels():
        tick.set_fontweight("bold")
    for tick in zoom.get_yticklabels():
        tick.set_fontweight("bold")
    # Mark the exact source window for the inset with a compact red ROI frame,
    # then connect its left corners to the inset.
    zoom_ymin, zoom_ymax = zoom.get_ylim()
    curve.add_patch(Rectangle(
        (zoom_start, zoom_ymin), 40_000 - zoom_start,
        zoom_ymax - zoom_ymin, facecolor="none", edgecolor="#d62728",
        linewidth=1.8, linestyle="-", zorder=5,
    ))
    fig.subplots_adjust(left=0.10, right=0.98, top=0.97, bottom=0.19)
    fig.canvas.draw()  # finalize inset and parent transforms before routing the callout
    curve.yaxis.get_offset_text().set_color("#c62828")
    curve.yaxis.get_offset_text().set_fontweight("bold")
    guide_color = "#555555"
    for inset_corner, focus_y in (((1, 1), zoom_ymax), ((1, 0), zoom_ymin)):
        fig.add_artist(ConnectionPatch(
            xyA=inset_corner, coordsA=zoom.transAxes,
        xyB=(zoom_start, focus_y), coordsB=curve.transData,
            axesA=zoom, axesB=curve, color=guide_color, linewidth=1.5,
            zorder=3, clip_on=False,
        ))
    zoom.spines[:].set_color("#555555")
    legend_handles = line_handles + [stop_handle]
    legend_labels = [
        "With TDR (Topup + EWMA)", "Without TDR (Plain)",
        stop_handle.get_label(),
    ]
    legend = fig.legend(legend_handles, legend_labels, loc="lower center",
                        bbox_to_anchor=(0.5, 0.045), ncol=3, fontsize=8,
                        frameon=True, framealpha=0.95)
    for item in legend.get_texts():
        item.set_fontweight("bold")
    for ext in ("png", "pdf"):
        fig.savefig(output / f"{stem}.{ext}", bbox_inches="tight")
    plt.close(fig)

    metadata = {
        "source_report": str(RUN / "report.json"),
        "source_summary": str(session_files[0]),
        "broker_idx": BROKER,
        "workload_ctx": 40_000,
        "gas_wei_per_broker_signed_tx": GAS_WEI,
        "broker_fee_share": FEE_SHARE,
        "fee_income_wei_per_served_ctx": FEE_INCOME_WEI,
        "plain_last_broker_service_at_ctx": last_plain_service,
        "first_plain_balance_shortfall": {
            "transaction": first_shortfall_pos,
            "broker_idx": BROKER,
            "destination_shard": int(first_shortfall["dst"]),
            "amount_wei": int(first_shortfall["amount_wei"]),
            "route": first_shortfall["route"],
        },
        "plain_flat_remaining_ctx": 40_000 - last_plain_service,
        "tdr_services_after_plain_plateau": tdr_tail_service,
        "arms": totals,
        "source_gates": {"V1_integrity": True,
                         "V2_relay_reduction": report["verdicts"]["V2_final_relay_median_le_cap"]["pass"]},
        "timing_note": ("Legacy exp008 tdr_moves.csv has no event arrival positions. Fee income is "
                        "accumulated at observed broker-service CTX positions; TDR burn gas is "
                        "evenly spread across arrivals. The curve is an estimated net-profit "
                        "trajectory, with exact terminal net profit."),
        "cost_note": ("exp008 B2E is disabled, so each served CTX has one broker-signed "
                      "Theta2 leg; its gas fee is used only as the 10% fee-income basis, not "
                      "deducted again as broker cost under the requested accounting. Each TDR "
                      "transfer has one broker-signed burn gas cost. Sender and coordinator "
                      "signed legs are excluded.")
    }
    (output / f"{stem}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
