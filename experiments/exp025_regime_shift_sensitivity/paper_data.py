"""Validate and export measured figure inputs; never synthesize missing timings."""
from __future__ import annotations

import csv
import json
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ETH = 10**18


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_arm(stats, arm_dir, workload):
    rows = read_csv(arm_dir / "ctx_rows.csv")
    ids = [r["ctx_id"] for r in rows]
    if len(rows) != len(workload) or len(set(ids)) != len(ids):
        raise ValueError(f"CTX records incomplete or duplicated: expected={len(workload)}, "
                         f"recorded={len(rows)}, unique={len(set(ids))}, "
                         f"failed={stats.get('failed')}, "
                         f"coordinator_aborted={stats.get('coord', {}).get('aborted')}; "
                         "see coordinator.json and execution_summary.json")
    expected = {r["ctx_id"]: r for r in workload}
    if set(ids) != set(expected):
        raise ValueError("Missing or extra CTX records")
    for row in rows:
        source = expected[row["ctx_id"]]
        for key, original in (("src", "src_shard"), ("dst", "dst_shard"),
                              ("arrival_pos", "arrival_pos"), ("amount_wei", "amount_wei")):
            if int(row[key]) != int(source[original]):
                raise ValueError(f"CTX workload changed: {row['ctx_id']} / {key}")
        if row["ok"].lower() != "true" or row["route"] not in ("broker", "relay"):
            raise ValueError("Failed CTX cannot be included in paper data")
        for key in ("submit_block", "t1_block", "t2_block", "xmit_bytes"):
            if row.get(key) in (None, "", "None"):
                raise ValueError(f"Missing measured {key}")
    if (stats["n"] != len(workload) or not stats["gates"]
            or not all(stats["gates"].values()) or not stats["burn_reconcile"]["ok"]
            or stats["failed"] or stats["served"] + stats["relayed"] != len(workload)
            or stats["tdr"]["transfers_lost"] or stats["tdr"]["lost_in_transit_wei"]):
        raise ValueError("Integrity/conservation validation failed")
    moves = read_csv(arm_dir / "tdr_moves.csv")
    if len(moves) != stats["tdr"]["transfers_done"]:
        raise ValueError("TDR ledger count mismatch")
    for row in moves:
        if row["state"] != "done" or min(int(row["burn_block"]), int(row["mint_block"])) < 0:
            raise ValueError("Incomplete or invalid TDR event")
    return rows, moves


def export_arm_data(cfg, stats, arm_dir, workload):
    rows, moves = validate_arm(stats, arm_dir, workload)
    deltas = defaultdict(lambda: defaultdict(int))
    blocks = defaultdict(lambda: dict(broker_ctx=0, relay_ctx=0, xmit_bytes=0, tdr_transfers=0))
    for row in rows:
        blocks[int(row["submit_block"])][row["route"] + "_ctx"] += 1
        blocks[int(row["submit_block"])]["xmit_bytes"] += int(row["xmit_bytes"])
        if row["route"] == "broker":
            deltas[int(row["t1_block"])][int(row["src"])] += (
                int(row["amount_wei"]) + int(row.get("fee_broker_wei", 0)))
            deltas[int(row["t2_block"])][int(row["dst"])] -= int(row["amount_wei"])
    for row in moves:
        deltas[int(row["burn_block"])][int(row["src_shard"])] -= int(row["amount_wei"])
        deltas[int(row["mint_block"])][int(row["dst_shard"])] += int(row["amount_wei"])
        blocks[int(row["mint_block"])]["tdr_transfers"] += 1
    last = max([int(r["t2_block"]) for r in rows] + list(deltas) + list(blocks))
    balance = [int(Decimal(str(stats["fund_eth"])) * ETH) * cfg.scale.num_brokers] * cfg.chain.num_shards
    fields = ["block"] + [f"shard_{s}_balance_eth" for s in range(cfg.chain.num_shards)]
    balance_rows = []
    for block in range(last + 1):
        for shard, delta in deltas[block].items():
            balance[shard] += delta
        balance_rows.append({"block": block, **{
            f"shard_{s}_balance_eth": str(Decimal(value) / ETH)
            for s, value in enumerate(balance)}})
    expected_total = int(Decimal(str(stats["fund_eth"])) * ETH) * cfg.scale.num_brokers * cfg.chain.num_shards
    expected_total += sum(int(r.get("fee_broker_wei", 0)) for r in rows)
    if sum(balance) != expected_total:
        raise ValueError("Reconstructed aggregate balance does not conserve retained fees")
    write_csv(arm_dir / "balance_by_block.csv", balance_rows, fields)
    write_csv(arm_dir / "block_stats.csv", [dict(block=b, **blocks[b]) for b in sorted(blocks)],
              ["block", "broker_ctx", "relay_ctx", "xmit_bytes", "tdr_transfers"])
    if cfg.exp.get("profit_broker_idx") is not None:
        export_profit(rows, moves, arm_dir, int(cfg.exp["profit_broker_idx"]),
                      int(cfg.exp["profit_tdr_cost_wei"]))


def export_profit(rows, moves, arm_dir, broker_idx, cost_per_transfer):
    """Actual retained fee income minus a declared nominal cost at recorded burn blocks.

    Map each burn to the highest arrival index confirmed by that block. Tail costs
    enter at the final CTX position. This is a documented index projection, not an
    invented event timestamp. Chain gasPrice remains zero.
    """
    if cost_per_transfer < 0:
        raise ValueError("Negative nominal TDR cost")
    ordered = sorted(rows, key=lambda r: int(r["arrival_pos"]))
    confirmations = sorted((int(r["t2_block"]), int(r["arrival_pos"]) + 1) for r in rows)
    times, positions = [], []
    highest = 0
    for block, position in confirmations:
        highest = max(highest, position)
        times.append(block)
        positions.append(highest)
    charges = defaultdict(int)
    for move in moves:
        if int(move["broker_idx"]) != broker_idx:
            continue
        index = bisect_right(times, int(move["burn_block"])) - 1
        position = positions[index] if index >= 0 else 1
        charges[max(1, min(len(rows), position))] += cost_per_transfer
    services = income = costs = 0
    exported = []
    for position, row in enumerate(ordered, 1):
        if row["route"] == "broker" and int(row["broker_idx"]) == broker_idx:
            services += 1
            income += int(row.get("fee_broker_wei", 0))
        costs += charges[position]
        exported.append(dict(transaction=position, broker_services=services,
                             fee_income_wei=income, modeled_tdr_cost_wei=costs,
                             net_profit_wei=income-costs))
    write_csv(arm_dir / "profit_by_tx.csv", exported, list(exported[0]))
    metadata = dict(broker_idx=broker_idx, cost_per_tdr_transfer_wei=cost_per_transfer,
                    actual_chain_tdr_gas_cost_wei=0, fee_income_wei=income,
                    modeled_tdr_cost_wei=costs, net_profit_wei=income-costs,
                    accounting="Recorded retained B2E fees minus explicitly modeled broker burn cost; not actual gas profit.",
                    timing="Cost uses measured burn blocks projected to the highest confirmed arrival position; tail costs at final CTX.")
    (arm_dir / "profit_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
