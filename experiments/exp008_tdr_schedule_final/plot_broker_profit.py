#!/usr/bin/env python3
"""Plot one broker's estimated fee revenue from an existing exp008 run.

No chain is started. A broker route earns 10% of a fixed 21,000,000 wei
nominal gas fee; relay routes earn zero. This is a post-hoc estimate, not
chain-collected fees or economic net profit.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAS_FEE_WEI = 21_000_000
BROKER_FEE_WEI = GAS_FEE_WEI // 10
PLAIN = "plain@150.0"
TDR = "topup@150.0@0.95@20.0"


def existing_run() -> Path:
    candidates = sorted((p for p in (HERE / "out").iterdir()
                         if (p / "report.json").is_file()), reverse=True)
    for path in candidates:
        report = json.loads((path / "report.json").read_text(encoding="utf-8"))
        if (report.get("verdicts", {}).get("V1_gates_every_session") is True
                and PLAIN in report.get("arms", {}) and TDR in report.get("arms", {})):
            return path
    raise FileNotFoundError("No complete exp008 report with Plain and final TDR arms")


def read_arm(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(r["ok"].lower() not in ("true", "1") for r in rows):
        raise ValueError(f"Missing or unsuccessful transactions in {path}")
    rows.sort(key=lambda r: int(r["arrival_pos"]))
    if [int(r["arrival_pos"]) for r in rows] != list(range(len(rows))):
        raise ValueError(f"Nonconsecutive or duplicate arrival positions in {path}")
    if any(r["route"] not in ("broker", "relay") for r in rows):
        raise ValueError(f"Unexpected route in {path}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="exp008 out/<timestamp> directory")
    parser.add_argument("--broker", type=int, default=44)
    parser.add_argument("--session", type=int, default=1)
    args = parser.parse_args()
    run = (args.run or existing_run()).resolve()
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    if report.get("verdicts", {}).get("V1_gates_every_session") is not True:
        raise ValueError("Source run failed exp008 integrity gates")
    sessions = list((run / f"session_{args.session}").glob("*/summary.json"))
    if len(sessions) != 1:
        raise ValueError("Expected exactly one source session summary")
    session = sessions[0].parent
    summary = json.loads(sessions[0].read_text(encoding="utf-8"))
    if summary.get("passed") is not True:
        raise ValueError("Source session did not pass")
    plain = read_arm(session / PLAIN / "ctx_rows.csv")
    tdr = read_arm(session / TDR / "ctx_rows.csv")
    if len(plain) != len(tdr) or any(
        p["ctx_id"] != t["ctx_id"] or p["arrival_pos"] != t["arrival_pos"]
        for p, t in zip(plain, tdr)
    ):
        raise ValueError("Plain and TDR workloads are not paired")

    broker = args.broker
    if broker < 0 or broker >= int(report["params"]["scale"]["num_brokers"]):
        raise ValueError("Invalid broker index")
    selected = []
    served = {"plain": 0, "tdr": 0}
    last_plain = None
    for n, (p, t) in enumerate(zip(plain, tdr), 1):
        p_served = p["route"] == "broker" and int(p["broker_idx"]) == broker
        t_served = t["route"] == "broker" and int(t["broker_idx"]) == broker
        served["plain"] += int(p_served)
        served["tdr"] += int(t_served)
        if p_served:
            last_plain = n
        selected.append({"transactions": n,
                         "plain_served": served["plain"],
                         "with_tdr_served": served["tdr"],
                         "plain_profit_wei": served["plain"] * BROKER_FEE_WEI,
                         "with_tdr_profit_wei": served["tdr"] * BROKER_FEE_WEI})
    if last_plain is None:
        raise ValueError("Selected broker never served a Plain transaction")
    last_plain_by_broker = {}
    for n, row in enumerate(plain, 1):
        if row["route"] == "broker":
            last_plain_by_broker[int(row["broker_idx"])] = n
    earliest_plain_stop = min(last_plain_by_broker, key=last_plain_by_broker.get)
    tdr_at_plateau = selected[last_plain - 1]["with_tdr_served"]
    if served["tdr"] <= tdr_at_plateau:
        raise ValueError("Selected broker has no TDR revenue growth after Plain plateau")

    out = HERE / "figs"
    out.mkdir(exist_ok=True)
    stem = f"exp008_broker{broker}_profit"
    csv_path = out / f"{stem}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [0] + [r["transactions"] for r in selected]
    plain_y = [0] + [r["plain_profit_wei"] / 1e9 for r in selected]
    tdr_y = [0] + [r["with_tdr_profit_wei"] / 1e9 for r in selected]
    fig, (whole, tail) = plt.subplots(1, 2, figsize=(12, 5), dpi=180,
                                     gridspec_kw={"width_ratios": [1.5, 1]})
    for ax in (whole, tail):
        ax.grid(alpha=0.25)
        ax.set_xlabel("Transactions in exp008 arrival order")
    whole.plot(x, tdr_y, color="#df320b", lw=2.2, label="With TDR (Topup + EWMA)")
    whole.plot(x, plain_y, color="#28a5ed", lw=2.2, label="Without TDR (Plain)")
    whole.axvline(last_plain, color="#666666", lw=1.2, ls="--")
    whole.set_ylabel("Estimated cumulative broker fee (gwei)")
    whole.set_xlim(0, len(selected))
    whole.set_ylim(bottom=0)
    whole.set_title(f"Broker {broker}: full run" +
                    (" (earliest Plain stop of 50)" if broker == earliest_plain_stop else ""))
    whole.legend(loc="upper left", fontsize=8)
    tail.plot(x[last_plain:],
              [v - tdr_y[last_plain] for v in tdr_y[last_plain:]],
              color="#df320b", lw=2.2, label="With TDR")
    tail.plot(x[last_plain:],
              [v - plain_y[last_plain] for v in plain_y[last_plain:]],
              color="#28a5ed", lw=2.2, label="Without TDR")
    tail.set_xlim(last_plain, len(selected))
    tail.set_ylim(bottom=-0.003)
    tail.set_ylabel("Additional fee after Plain stops (gwei)")
    tail.set_title(f"Final {len(selected) - last_plain:,} transactions")
    tail.legend(loc="upper left", fontsize=8)
    fig.suptitle("exp008 observed routes; fixed 21,000,000 wei gas fee × 10% per broker service",
                 fontsize=10)
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(out / f"{stem}.{suffix}", bbox_inches="tight")
    plt.close(fig)

    metadata = {
        "source_report": str(run / "report.json"), "source_session": str(sessions[0]),
        "broker_idx": broker, "transactions": len(selected),
        "gas_fee_wei_per_transaction": GAS_FEE_WEI,
        "broker_share": 0.10, "broker_profit_wei_per_served_transaction": BROKER_FEE_WEI,
        "plain_served": served["plain"], "with_tdr_served": served["tdr"],
        "plain_last_served_transaction": last_plain,
        "earliest_plain_stop_broker": earliest_plain_stop,
        "selection_note": ("Selected after inspecting all 50 brokers for the earliest final Plain service."
                           if broker == earliest_plain_stop else "Broker specified by user or CLI."),
        "plain_flat_transactions": len(selected) - last_plain,
        "with_tdr_served_after_plain_plateau": served["tdr"] - tdr_at_plateau,
        "plain_final_profit_wei": selected[-1]["plain_profit_wei"],
        "with_tdr_final_profit_wei": selected[-1]["with_tdr_profit_wei"],
        "note": "Post-hoc estimated fee revenue from observed routes; exp008 did not charge B2E fees."
    }
    (out / f"{stem}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    print(f"Figure: {out / (stem + '.png')}")


if __name__ == "__main__":
    main()
