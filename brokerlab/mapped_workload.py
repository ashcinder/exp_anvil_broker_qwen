"""Exp012: fixed suffix-bucket placement and auditable trace workloads.

Placement applies identically to both endpoints. It creates spatial concentration,
not guaranteed net directional flow. No state is migrated during a run.
"""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from .config import ETH
from .real_data import _flag_is_true, _is_contract_creation


def placement(address, bits, hotspots, num_shards=16):
    if num_shards != 16 or type(bits) is not int or not 4 <= bits <= 8:
        raise ValueError("Suffix placement requires 16 shards and 4..8 bits")
    if not hotspots or len(set(hotspots)) != len(hotspots):
        raise ValueError("Hotspots must be nonempty and unique")
    if any(type(s) is not int or not 0 <= s < 16 for s in hotspots):
        raise ValueError("Hotspot IDs must be integers in 0..15")
    if len(hotspots) not in (1, 2, 3, 4, 8):
        raise ValueError("Use 1, 2, 3, 4 or 8 hotspots")
    n = int(address, 16)
    bucket = n & ((1 << bits) - 1)
    # The first 16 buckets preserve the old rule; additional buckets go hot.
    return bucket if bucket < 16 else hotspots[(bucket - 16) % len(hotspots)]


def theoretical(scenario):
    bits, hot = scenario["bits"], scenario["hotspots"]
    counts = Counter(placement(hex(n), bits, hot) for n in range(1 << bits))
    p = [counts[s] / (1 << bits) for s in range(16)]
    cross = 1 - sum(x * x for x in p)
    return {"address_shares": p, "hot_address_share": sum(p[s] for s in hot),
            "iid_cross_fraction": cross,
            # CTX is conditioned on src != dst; concentrated endpoints cancel.
            "iid_ctx_endpoint_shares": [x * (1 - x) / cross for x in p],
            "bucket_counts": [counts[s] for s in range(16)]}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def candidate_pool(cfg, count, *, skip=0):
    """Chronological pre-sharding window; skip eligible unique candidates.

    Deduplication includes the skipped prefix so held-out windows cannot reuse
    transaction IDs from training. Existing callers retain prefix semantics.
    """
    if count <= 0 or skip < 0:
        raise ValueError("count must be positive and skip nonnegative")
    out, reasons, seen = [], Counter(), set()
    floor = int(cfg.traffic.value_floor_eth * ETH)
    cap = int(cfg.traffic.value_cap_eth * ETH)
    with open(cfg.traffic.real_csv_path, newline="", encoding="utf-8-sig") as f:
        for line, row in enumerate(csv.DictReader(f), 2):
            reasons["raw_rows_scanned"] += 1
            try:
                a, b, v = row["from"], row["to"], int(row["value"])
                ai, bi = int(a, 16), int(b, 16)
                txid = row["transactionHash"]
                if not txid or not 0 <= ai < 2**160 or not 0 <= bi < 2**160:
                    raise ValueError("Invalid address or hash")
            except (KeyError, ValueError, TypeError):
                reasons["invalid"] += 1
                continue
            if _is_contract_creation(row.get("toCreate")) or _flag_is_true(row.get("isError")):
                reasons["creation_or_error"] += 1
                continue
            if not cfg.traffic.allow_contract_endpoints and (
                    _flag_is_true(row.get("fromIsContract")) or _flag_is_true(row.get("toIsContract"))):
                reasons["contract_endpoint"] += 1
                continue
            if not floor <= v <= cap:
                reasons["amount_filtered"] += 1
                continue
            sender = cfg.scale.user_base_index + ai % cfg.scale.num_users
            receiver = cfg.scale.user_base_index + bi % cfg.scale.num_users
            if sender == receiver:
                reasons["simulated_account_collision"] += 1
                continue
            if txid in seen:
                reasons["duplicate_hash"] += 1
                continue
            seen.add(txid)
            if len(seen) <= skip:
                reasons["eligible_candidates_skipped"] += 1
                continue
            out.append({"ctx_id": "real_" + txid, "orig_from": a, "orig_to": b,
                        "amount_wei": v, "sender_idx": sender, "receiver_idx": receiver,
                        "source_line": line, "source_block": row.get("blockNumber"),
                        "source_timestamp": row.get("timestamp")})
            if len(out) == count:
                break
    if len(out) < count:
        raise ValueError(f"Only {len(out)} eligible pre-mapping rows; requested {count}")
    reasons["accepted_candidates"] = len(out)
    return out, dict(reasons)


def flow_stats(rows, cfg):
    counts = [[0] * 16 for _ in range(16)]
    amounts = [[0] * 16 for _ in range(16)]
    addresses = [set() for _ in range(16)]
    for r in rows:
        s, d, v = r["src_shard"], r["dst_shard"], r["amount_wei"]
        counts[s][d] += 1
        amounts[s][d] += v
        addresses[s].add(r["orig_from"].lower())
        addresses[d].add(r["orig_to"].lower())
    n, total = len(rows), sum(sum(x) for x in amounts)
    per = []
    capacity = cfg.chain.gas_limit // 21000
    for s in range(16):
        src, dst = sum(counts[s]), sum(r[s] for r in counts)
        sent, received = sum(amounts[s]), sum(r[s] for r in amounts)
        share = (src + dst) / (2 * n) if n else 0
        per.append({"shard": s, "unique_addresses": len(addresses[s]),
                    "src_count": src, "dst_count": dst, "endpoint_share": share,
                    "user_sent_wei": sent, "user_received_wei": received,
                    "user_net_in_wei": received - sent,
                    "broker_potential_net_change_wei": sent - received,
                    "target_ctx_legs_per_block": 2 * cfg.exp["rate"] * share,
                    "target_capacity_ratio": 2 * cfg.exp["rate"] * share / capacity})
    return {"n": n, "total_wei": total, "count_matrix": counts, "amount_matrix_wei": amounts,
            "per_shard": per, "intra_count": sum(counts[s][s] for s in range(16)),
            "endpoint_hhi": sum(p["endpoint_share"]**2 for p in per),
            "peak_endpoint_share": max(p["endpoint_share"] for p in per),
            "net_imbalance_ratio": sum(abs(p["user_net_in_wei"]) for p in per) / (2 * total) if total else 0,
            "max_target_capacity_ratio": max(p["target_capacity_ratio"] for p in per)}


def prepare_scenario(pool, scenario, cfg, count, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mapped = []
    for r in pool:
        mapped.append({**r,
                       "src_shard": placement(r["orig_from"], scenario["bits"], scenario["hotspots"]),
                       "dst_shard": placement(r["orig_to"], scenario["bits"], scenario["hotspots"])})
    cross = [r for r in mapped if r["src_shard"] != r["dst_shard"]]
    if len(cross) < count:
        raise ValueError(f"{scenario['name']}: only {len(cross)} CTX; increase exp.candidate_count")
    selected = [{**r, "arrival_pos": i} for i, r in enumerate(cross[:count])]
    audit = {"scenario": scenario, "theoretical_uniform_address_model": theoretical(scenario),
             "candidate_pool": flow_stats(mapped, cfg), "selected_ctx": flow_stats(selected, cfg),
             "selection": {"candidate_count": len(pool), "cross_count": len(cross),
                           "intra_fraction": 1 - len(cross) / len(pool),
                           "first_source_line": selected[0]["source_line"],
                           "last_source_line": selected[-1]["source_line"],
                           "first_source_block": selected[0]["source_block"],
                           "last_source_block": selected[-1]["source_block"],
                           "amount_above_initial_balance_count": sum(
                               r["amount_wei"] > int(cfg.exp["balances_eth"] * ETH) for r in selected)}}
    payload = {"schema": 1, "num_shards": cfg.chain.num_shards,
               "num_users": cfg.scale.num_users, "user_base_index": cfg.scale.user_base_index,
               "scenario": scenario, "rows": selected}
    path = directory / "workload.json"
    write_json(path, payload)
    audit["workload_sha256"] = sha256_file(path)
    window = int(cfg.exp["rate"] * cfg.exp["tdr_window_blocks"])
    audit["arrival_windows"] = [
        {"first_arrival_pos": start, "last_arrival_pos": min(start + window, len(selected)) - 1,
         **flow_stats(selected[start:start + window], cfg)}
        for start in range(0, len(selected), window)]
    write_json(directory / "workload_audit.json", audit)
    with open(directory / "mapping_buckets.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["suffix_binary", "bucket", "shard"])
        for n in range(1 << scenario["bits"]):
            writer.writerow([format(n, f"0{scenario['bits']}b"), n,
                             placement(hex(n), scenario["bits"], scenario["hotspots"])])
    return path, audit


def load_prepared(cfg):
    path = Path(cfg.exp["prepared_workload_path"])
    if sha256_file(path) != cfg.exp["prepared_workload_sha256"]:
        raise ValueError("Prepared workload SHA256 mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") == 4:
        from .two_shard_factorial import validate_two_shard
        return validate_two_shard(payload, cfg)
    if payload.get("schema") == 3:
        from .controlled_direction import validate_controlled
        return validate_controlled(payload, cfg)
    if payload.get("schema") == 2:
        from .directional_workload import validate_prepared
        return validate_prepared(payload, cfg)
    for key, value in (("schema", 1), ("num_shards", cfg.chain.num_shards),
                       ("num_users", cfg.scale.num_users), ("user_base_index", cfg.scale.user_base_index)):
        if payload.get(key) != value:
            raise ValueError(f"Prepared workload incompatible {key}")
    rows, ids = payload["rows"], set()
    expected = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    if len(rows) != expected:
        raise ValueError(f"Expected {expected} CTX, got {len(rows)}")
    scenario = payload["scenario"]
    for i, r in enumerate(rows):
        if (r["ctx_id"] in ids or r["arrival_pos"] != i or r["src_shard"] == r["dst_shard"]
                or r["sender_idx"] == r["receiver_idx"]):
            raise ValueError("Invalid CTX identity, order or cross-shard route")
        ids.add(r["ctx_id"])
        for address, shard, user in ((r["orig_from"], r["src_shard"], r["sender_idx"]),
                                     (r["orig_to"], r["dst_shard"], r["receiver_idx"])):
            if shard != placement(address, scenario["bits"], scenario["hotspots"]):
                raise ValueError("Prepared address placement mismatch")
            if user != cfg.scale.user_base_index + int(address, 16) % cfg.scale.num_users:
                raise ValueError("Prepared account index mismatch")
        if not int(cfg.traffic.value_floor_eth * ETH) <= r["amount_wei"] <= int(cfg.traffic.value_cap_eth * ETH):
            raise ValueError("Prepared amount outside configured limits")
    return rows, sum(r["amount_wei"] for r in rows)


def collect_blocks(conns, rows, arm_dir, num_shards):
    """Post-run read-only scan; outside measured service wall time.

    Blocks from first to last CTX confirmation, inclusive, including TDR gas
    mined in that interval. Later TDR tails are intentionally excluded.
    """
    heights = [int(r[k]) for r in rows for k in ("t1_block", "t2_block") if r.get(k)]
    if not heights:
        raise ValueError("No confirmed CTX blocks to audit")
    lo, requested_hi = min(heights), max(heights)
    # Independently mined chains can differ in height. Use only a common,
    # already mined interval, so missing future blocks cannot fail the run.
    hi = min(requested_hi, min(conns.web3(s).eth.block_number for s in range(num_shards)))
    if hi < lo:
        raise ValueError("No common mined CTX block interval")
    per = []
    with open(Path(arm_dir) / "block_capacity.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["shard", "block", "gas_used", "gas_limit", "utilization", "transactions"])
        for shard in range(num_shards):
            util, full = [], 0
            for h in range(lo, hi + 1):
                b = conns.web3(shard).eth.get_block(h)
                u = int(b["gasUsed"]) / int(b["gasLimit"])
                util.append(u)
                full += int(int(b["gasLimit"]) - int(b["gasUsed"]) < 21000)
                writer.writerow([shard, h, b["gasUsed"], b["gasLimit"], u, len(b["transactions"])])
            hops = [int(r[key]) for r in rows for key, side in (("t1_hops", "src"), ("t2_hops", "dst"))
                    if r.get(side) == shard and r.get(key) is not None]
            hops.sort()
            per.append({"shard": shard, "mean_utilization": sum(util) / len(util),
                        "max_utilization": max(util), "full_blocks": full,
                        "full_block_fraction": full / len(util),
                        "ctx_half_hops_p95": hops[round(.95 * (len(hops) - 1))] if hops else None,
                        "ctx_half_hops_gt1_fraction": sum(h > 1 for h in hops) / len(hops) if hops else None})
    return {"start_block": lo, "end_block": hi, "last_ctx_block": requested_hi, "per_shard": per,
            "max_full_block_fraction": max(p["full_block_fraction"] for p in per),
            "max_mean_utilization": max(p["mean_utilization"] for p in per)}
