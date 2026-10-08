"""Plot broker 0's two individual sub-accounts from verified exp027 CSV exports."""
import argparse
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator


def plain_transaction_balances(root, report, arm, out):
    """Settled balance accounting by input prefix, not a wall-clock snapshot.

    Each successful broker CTX contributes both settled legs together. Relay CTXs
    count on the x axis but do not change broker balances. Preserve integer wei.
    """
    path = root/arm["arm"]/"ctx_rows.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = sorted(csv.DictReader(stream), key=lambda row: int(row["arrival_pos"]))
    if len(rows) != arm["n"] or len({r["ctx_id"] for r in rows}) != len(rows):
        raise ValueError("Incomplete or duplicated transaction records")
    if [int(r["arrival_pos"]) for r in rows] != list(range(len(rows))):
        raise ValueError("Transaction input positions must be contiguous")
    with (root/arm["arm"]/"tdr_moves.csv").open(encoding="utf-8-sig", newline="") as stream:
        if list(csv.DictReader(stream)):
            raise ValueError("Plain transaction accounting requires no TDR moves")
    balance = [int(Decimal(str(arm["fund_eth"])) * 10**18)] * 2
    exported = []

    def append(position):
        exported.append(dict(transaction=position, **{
            f"shard_{shard}_balance_eth": str(Decimal(value) / 10**18)
            for shard, value in enumerate(balance)}))

    append(0)
    for position, row in enumerate(rows, 1):
        if row["ok"].lower() != "true" or row["route"] not in ("broker", "relay"):
            raise ValueError("Require successful broker or relay records")
        if row["route"] == "broker":
            if int(row["broker_idx"]) != 0:
                raise ValueError("Require broker 0 individual records")
            amount = int(row["amount_wei"])
            balance[int(row["src"])] += amount + int(row["fee_broker_wei"])
            balance[int(row["dst"])] -= amount
        append(position)
    # Check exact terminal values against the existing event/block ledger.
    with (root/arm["arm"]/"balance_by_block.csv").open(encoding="utf-8-sig", newline="") as stream:
        final = list(csv.DictReader(stream))[-1]
    for shard in range(2):
        if Decimal(exported[-1][f"shard_{shard}_balance_eth"]) != Decimal(final[f"shard_{shard}_balance_eth"]):
            raise ValueError("Transaction and block ledgers disagree at the end")
    with (out/"fig1b_balance_by_transaction.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(exported[0]))
        writer.writeheader()
        writer.writerows(exported)
    return path, exported


def paper_right(root, report, out, x_axis="transactions", display_end=None):
    """Standalone manuscript Fig1b, using Plain broker 0's measured event ledger."""
    arm = next(a for a in report["arms"] if a["engine"] == "plain")
    if not all(arm["gates"].values()) or not arm["burn_reconcile"]["ok"] or arm["failed"]:
        raise ValueError("Plain data did not pass experiment validation")
    if x_axis == "transactions":
        path, rows = plain_transaction_balances(root, report, arm, out)
        coordinate = "transaction"
    else:
        path = root/arm["arm"]/"balance_by_block.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        coordinate = "block"
    src, dst = int(report["params"]["exp"]["source_shard"]), int(report["params"]["exp"]["destination_shard"])
    blocks = [int(row[coordinate]) for row in rows]
    source = [float(row[f"shard_{src}_balance_eth"]) for row in rows]
    destination = [float(row[f"shard_{dst}_balance_eth"]) for row in rows]
    observed_end = blocks[-1]
    if display_end is not None and display_end < observed_end:
        raise ValueError("Display end must include every recorded balance block")
    visible_end = display_end if display_end is not None else observed_end
    axis_end = visible_end + max(3, int(visible_end * .025))
    # Half of the measured manuscript motivation page: 537.06 x 498.14 points.
    fig, ax = plt.subplots(figsize=(537.06/72, 498.14/72), dpi=300)
    fig.subplots_adjust(left=.20, right=.97, bottom=.20, top=.95)
    ax.step(blocks, source, where="post", color="#28a5ed", lw=3.2,
            linestyle=(0, (2, 2)), label=f"Shard {src} (source)")
    ax.step(blocks, destination, where="post", color="#28a5ed", lw=3.2,
            label=f"Shard {dst} (destination)")
    if display_end is not None and display_end > observed_end:
        for value, linestyle in ((source[-1], (0, (2, 2))), (destination[-1], "-")):
            ax.plot([observed_end, display_end], [value, value],
                    color="#28a5ed", lw=3.2, linestyle=linestyle)
    ax.set_xlim(0, axis_end)
    ax.set_ylim(-3, max(source + destination) * 1.13)
    ax.set_xlabel("# of transactions" if x_axis == "transactions" else "Block heights", fontsize=30, labelpad=12)
    ax.set_ylabel("Broker 0 sub-account balance (ETH)", fontsize=21, labelpad=12)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    if display_end is not None:
        ax.set_xticks(range(0, display_end + 1, max(1, display_end // 5)))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.tick_params(axis="x", labelsize=23, width=1.5)
    ax.tick_params(axis="y", labelsize=24, width=1.5)
    ax.grid(linestyle=":", alpha=.45)
    ax.legend(loc="upper left", bbox_to_anchor=(.02, 1.0), fontsize=13,
              framealpha=1, edgecolor="#999999")
    floor = float(report["params"]["traffic"]["value_floor_eth"])
    low = next((i for i, value in enumerate(destination) if value < floor), None)
    if low is not None:
        low_block = blocks[low]
        plateau_end = display_end if display_end is not None else observed_end
        ax.add_patch(Rectangle((low_block, -.9), max(1, plateau_end - low_block),
                               4.0, fill=False, edgecolor="#FF0000", linewidth=2))
        ax.add_patch(Rectangle((.32, .27), .65, .42, transform=ax.transAxes,
                               fill=False, edgecolor="#FF0000", linewidth=2))
        ax.text(.645, .48,
                "As the balances of sub-\naccounts are depleted,\n"
                "the sharded blockchain\n" + r"falls back to the $\mathit{relay}$" +
                "\nmechanism [1],for handl-\ning those unserved CTXs",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=14, fontweight="bold", color="#FF0000", zorder=15)
        ax.annotate("", xy=(.645, .27), xycoords="axes fraction",
                    xytext=((low_block + plateau_end) / 2, 3.1), textcoords="data",
                    arrowprops={"arrowstyle": "->", "color": "#FF0000", "lw": 2})
    out.mkdir(parents=True, exist_ok=True)
    stem = "fig1b_broker0_two_shard_balance"
    for ext in ("png", "pdf"):
        fig.savefig(out/f"{stem}.{ext}", dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    metadata = dict(experiment="exp027", source_run=str(root), arm=arm["arm"], broker_idx=0,
                    data_path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    initial_balances_eth=[source[0], destination[0]],
                    terminal_balances_eth=[source[-1], destination[-1]],
                    x_axis=x_axis,
                    observed_balance_end=observed_end, display_axis_end=axis_end,
                    final_balance_continuation=(dict(start=observed_end, end=display_end,
                        style="Same blue, linewidth and source dashed / destination solid styles as legend", interpretation="Final recorded balances held constant for visual context; no additional blocks observed")
                        if display_end is not None and display_end > observed_end else None),
                    **{f"destination_first_below_trace_floor_{coordinate}": blocks[low] if low is not None else None},
                    trace_floor_eth=floor,
                    scope=("Broker 0 settled balance accounting by input transaction prefix; each broker CTX includes both settled legs. All CTXs, including Relay, count. Not chronological chain snapshots."
                           if x_axis == "transactions" else
                           "Broker 0 individual ledger balances reconstructed from actual confirmation events; not a sum across brokers. Independent shard heights."))
    (out/"fig1b_source_manifest.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    (out/"index.html").write_text('<!doctype html><meta charset="utf-8"><title>实验27 Fig1右图</title>'
                                  '<style>body{font-family:Arial;padding:30px}img{max-width:100%;max-height:850px}</style>'
                                  f'<h1>Figure 1(b): TDR OFF</h1><a href="{stem}.pdf">矢量PDF</a><br><img src="{stem}.png">', encoding="utf-8")
    print(f"Paper Fig1b saved: {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--paper-right", action="store_true", help="Draw standalone manuscript Fig1b from the Plain arm")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--x-axis", choices=("transactions", "blocks"),
                        help="Paper right defaults to transactions; paired TDR comparison uses blocks")
    parser.add_argument("--display-end", type=int,
                        help="Extend the paper-right axis holding final balances with each curve's legend style")
    args = parser.parse_args()
    if not args.paper_right and args.x_axis == "transactions":
        parser.error("Transaction-prefix accounting is for --paper-right (Plain); use blocks for the paired TDR plot")
    root = args.source.resolve()
    report = json.loads((root/"report.json").read_text(encoding="utf-8"))
    if report["experiment"] != "exp027" or not report["status"]["complete"]:
        raise ValueError("Require a completed exp027 run")
    if report["params"]["scale"]["num_brokers"] != 1:
        raise ValueError("Single-broker balance curves must not use multi-broker aggregates")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.weight": "bold",
                        "axes.labelweight": "bold", "font.size": 19, "pdf.fonttype": 42})
    out = args.output.resolve() if args.output else root/"figs"
    out.mkdir(parents=True, exist_ok=True)
    if args.paper_right:
        paper_right(root, report, out, args.x_axis or "transactions", args.display_end)
        return
    sources = []
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.8), sharey=True)
    src, dst = report["params"]["exp"]["source_shard"], report["params"]["exp"]["destination_shard"]
    for ax, arm in zip(axes, report["arms"]):
        path = root/arm["arm"]/"balance_by_block.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        sources.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        blocks = [int(row["block"]) for row in rows]
        for shard, colour, label in ((src, "#28a5ed", "Source"), (dst, "#df320b", "Destination")):
            balances = [float(row[f"shard_{shard}_balance_eth"]) for row in rows]
            ax.step(blocks, balances, where="post", color=colour, linewidth=2.7,
                    label=f"Shard {shard} ({label})")
        ax.axhline(arm["fund_eth"], color="#888888", linestyle=":", linewidth=1.2)
        ax.set_title("Plain" if arm["engine"] == "plain" else "Top-up + EWMA")
        ax.set_xlabel("Block heights")
        ax.grid(alpha=.25)
        ax.legend(loc="upper right", fontsize=13)
    axes[0].set_ylabel("Broker 0 sub-account balance (ETH)")
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out/f"broker0_two_shard_balances.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    (out/"source_manifest.json").write_text(json.dumps(dict(sources=sources,
        scope="One broker's individual ledger balances reconstructed at actual confirmation blocks; shard clocks are independent.",
        initial_balance_eth=report["params"]["exp"]["balances_eth"],
        direction=[src, dst]), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Figures saved: {out}")


if __name__ == "__main__":
    main()
