#!/usr/bin/env python3
"""Revalue an existing exp022 run at a fixed 21m-wei fee, without rerunning it.

The left panel is gross broker commission. The right panel subtracts fixed
gas costs for every broker-signed transaction: Θ1b fee burn and Θ2 service
transfer per broker-served CTX, plus one TDR burn per confirmed rebalance.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAS_WEI = 21_000_000
COMMISSION_WEI = GAS_WEI // 10
BROKER_SIGNED_SERVICE_LEGS = 2  # B2E Θ1b fee burn + Θ2 service transfer


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path,
                        default=HERE / "out" / "20260929_173509")
    args = parser.parse_args()
    run = args.run.resolve()
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("status") != "complete" or summary.get("mode") != "full"
            or summary.get("workload_count") != 15_000):
        raise ValueError("Expected the completed 15,000-CTX full exp022 run")
    for arm in ("plain", "tdr"):
        stats = summary["arms"][arm]["stats"]
        if not all(stats["gates"].values()) or not stats["burn_reconcile"]["ok"]:
            raise ValueError(f"{arm} failed integrity gates")
        if stats["tdr"]["transfers_lost"]:
            raise ValueError(f"{arm} contains lost TDR transfers")

    profit_rows = read_csv(run / "profit_by_tx.csv")
    balance_rows = read_csv(run / "balance_by_block.csv")
    burns = {(r["arm"], int(r["block"])): int(r["tdr_burn"])
             for r in balance_rows}
    if len(burns) != len(balance_rows):
        raise ValueError("Duplicate arm/block balance rows")
    by_arm = defaultdict(list)
    for row in profit_rows:
        arm, block = row["arm"], int(row["block"])
        if (arm, block) not in burns:
            raise ValueError(f"Missing burn count for {arm} block {block}")
        by_arm[arm].append(row)

    data = []
    metrics = {}
    for arm in ("plain", "tdr"):
        rows = sorted(by_arm[arm], key=lambda r: int(r["block"]))
        if not rows or int(rows[0]["confirmed_ctx"]) != 0:
            raise ValueError(f"{arm} is missing its zero baseline")
        tdr_burns = 0
        for row in rows:
            block = int(row["block"])
            tdr_burns += burns[arm, block]
            served = int(row["cumulative_broker_ctx"])
            gross = served * COMMISSION_WEI
            service_gas = served * BROKER_SIGNED_SERVICE_LEGS * GAS_WEI
            tdr_gas = tdr_burns * GAS_WEI
            net = gross - service_gas - tdr_gas
            data.append({"arm": arm, "block": block,
                         "confirmed_ctx": int(row["confirmed_ctx"]),
                         "broker_ctx": served, "tdr_burns": tdr_burns,
                         "gross_commission_wei": gross,
                         "broker_service_gas_wei": service_gas,
                         "tdr_gas_wei": tdr_gas,
                         "net_profit_wei": net})
        final = data[-1]
        stats = summary["arms"][arm]["stats"]
        if (final["confirmed_ctx"] != summary["workload_count"]
                or final["broker_ctx"] != stats["served"]
                or final["tdr_burns"] != stats["tdr"]["transfers_done"]):
            raise ValueError(f"{arm} accounting does not match summary")
        metrics[arm] = {
            "served": final["broker_ctx"], "relay": stats["relayed"],
            "tdr_burns": final["tdr_burns"],
            "gross_commission_wei": final["gross_commission_wei"],
            "broker_service_gas_wei": final["broker_service_gas_wei"],
            "tdr_gas_wei": final["tdr_gas_wei"],
            "net_profit_wei": final["net_profit_wei"]}
    plain = [r for r in data if r["arm"] == "plain"]
    plateau = next(r["confirmed_ctx"] for r in plain
                   if r["broker_ctx"] == metrics["plain"]["served"])
    if any(r["broker_ctx"] != metrics["plain"]["served"]
           for r in plain if r["confirmed_ctx"] >= plateau):
        raise ValueError("Plain plateau is not sustained")

    figs = run / "figs"
    figs.mkdir(exist_ok=True)
    stem = "fixed_gas_10pct_cumulative_earnings"
    with (figs / f"{stem}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), dpi=180, sharex=True)
    for ax, key, title in zip(
        axes,
        ("gross_commission_wei", "net_profit_wei"),
        ("Gross commission income", "Net broker profit after gas costs"),
    ):
        for arm, label, color in (("tdr", "With TDR", "#df320b"),
                                  ("plain", "Without TDR", "#28a5ed")):
            rows = [r for r in data if r["arm"] == arm]
            ax.plot([r["confirmed_ctx"] for r in rows],
                    [r[key] / 1e9 for r in rows],
                    label=label, color=color, linewidth=2.1)
        ax.axvline(plateau, ls="--", color="#666666", linewidth=1)
        ax.set(xlim=(0, 15_000),
               xlabel="Confirmed cross-shard transactions",
               ylabel=("Cumulative income (gwei)" if key == "gross_commission_wei"
                       else "Cumulative net profit (gwei)"), title=title)
        if key == "gross_commission_wei":
            ax.set_ylim(bottom=0)
        else:
            low = min(r[key] for r in data if r["arm"] in ("plain", "tdr")) / 1e9
            ax.set_ylim(bottom=low * 1.08, top=0)
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left")
    fig.suptitle("Existing exp022 run, revalued: one broker; 21,000,000 wei per gas leg; 10% retained",
                 fontsize=10)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(figs / f"{stem}.{suffix}", bbox_inches="tight")
    plt.close(fig)

    metadata = {
        "source_summary": str(run / "summary.json"),
        "source_workload_sha256": summary["workload_sha256"],
        "source_workload": "15,000 identical 0.02 ETH A-to-B transfers; synthetic",
        "source_fee_model": {
            "fee_broker_wei_per_served_ctx": summary["fee_broker_wei_per_served_ctx"],
            "note": "Original chain run used a different nominal fee; this plot revalues its observed routes."
        },
        "fixed_gas_wei_per_transaction": GAS_WEI,
        "broker_commission_share": 0.10,
        "commission_wei_per_served_ctx": COMMISSION_WEI,
        "tdr_gas_cost_wei_per_confirmed_burn": GAS_WEI,
        "broker_signed_service_gas_legs_per_served_ctx": BROKER_SIGNED_SERVICE_LEGS,
        "service_gas_legs": ["B2E Θ1b fee burn", "broker Θ2 payment"],
        "excluded_gas_costs": ["sender-signed Θ1a", "coordinator-signed TDR mint",
                               "coordinator-signed relay mint"],
        "plain_plateau_at_confirmed_ctx": plateau,
        "plain_flat_confirmed_ctx_count": 15_000 - plateau,
        "arms": metrics,
        "scope_note": ("Right panel is estimated broker net profit under the stated "
                       "fixed gas model. It subtracts gas for both broker-signed "
                       "service legs and broker-signed TDR burns. It excludes "
                       "sender/coordinator costs and principal movements. "
                       "The 21m-wei gas amount is a stipulated counterfactual; "
                       "the source Anvil run had gasPrice=0.")
    }
    (figs / f"{stem}.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
