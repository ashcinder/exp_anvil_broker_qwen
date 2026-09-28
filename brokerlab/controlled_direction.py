"""Exp017: synthetic per-transaction direction control, NOT address placement.

Ethereum endpoints/values/order are provenance; simulated (shard, account) pairs
are synthetic. A controlled transaction can use a different shard for the same
original address. Schema 3 deliberately does not weaken schema 2's static rule.
"""
import csv
import hashlib
import math
from pathlib import Path

from .config import ETH
from .directional_workload import mapped, cross_only, ensure_order, direction_audit
from .mapped_workload import write_json, sha256_file

KIND = "synthetic_transaction_control_bit_v1"


def validate_settings(probabilities, seed):
    if (not isinstance(probabilities, list) or not probabilities
            or any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)
            or len(set(probabilities)) != len(probabilities)):
        raise ValueError("control_probabilities must be distinct finite probabilities in [0,1]")
    if type(seed) is not int or seed < 0:
        raise ValueError("control_seed must be a nonnegative integer")


def route_fields(row, probability, seed, target):
    """A paired, deterministic Bernoulli bit shared across probability arms.

    The SHA256 draw depends on seed + transaction ID, never amount or outcomes.
    Integer thresholds give exact p=0/p=1 behavior. Larger p selects a superset.
    The swap-on-conflict rule keeps every baseline CTX cross-shard without
    dropping/replacing transactions or altering the original provenance fields.
    """
    src = int(row["orig_from"], 16) % 16
    dst = int(row["orig_to"], 16) % 16
    if src == dst:
        raise ValueError("Control requires the common baseline cross-shard pool")
    key = f"exp017-control-v1|{seed}|{row['ctx_id']}".encode("utf-8")
    draw = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    bit = int(draw < int(float(probability) * (1 << 64)))
    new_src = dst if bit and src == target else src
    new_dst = target if bit else dst
    return dict(base_src_shard=src, base_dst_shard=dst,
                control_draw_hex=f"{draw:016x}", control_bit=bit,
                source_reassigned=int(new_src != src), destination_changed=int(new_dst != dst),
                src_shard=new_src, dst_shard=new_dst)


def controlled_layouts(pool, cfg, count):
    probabilities, seed = cfg.exp["control_probabilities"], cfg.exp["control_seed"]
    validate_settings(probabilities, seed)
    common = cross_only(mapped(pool))[:count]
    if len(common) != count:
        raise ValueError("Insufficient common baseline CTX; increase candidate_count")
    target = cfg.exp["target_shard"]
    scenarios = []
    for p in probabilities:
        name = "control_p" + format(float(p), ".17g").replace(".", "_").replace("-", "m").replace("+", "p")
        rows = [{**r, **route_fields(r, p, seed, target)} for r in common]
        scenarios.append((name, rows, {}, dict(mapping_kind=KIND, control_probability=float(p),
                          control_seed=seed, target_shard=target,
                          source_conflict_rule="swap source shard to original destination shard",
                          design="synthetic destination override; not a fixed address mapping")))
    return scenarios, dict(mapping_kind=KIND, common_ctx_count=count,
        paired_rows_sha256=hashlib.sha256("\n".join(r["ctx_id"] for r in common).encode()).hexdigest(),
        probabilities=probabilities, control_seed=seed, target_shard=target,
        pairing="Identical original transactions, amounts, order and accounts across every probability; nested control bits",
        caveat="p is override probability, not final destination share; original addresses may occur on multiple simulated shards")


def export_controlled(directory, name, rows, mapping, info, cfg, source_manifest):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    rows = [{**r, "arrival_pos": i} for i, r in enumerate(rows)]
    payload = dict(schema=3, num_shards=16, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   scenario={"name": name, **info}, rows=rows)
    validate_controlled(payload, cfg)
    path = directory / "workload.json"
    write_json(path, payload)
    with (directory / "transactions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    audit = direction_audit(rows, cfg, cfg.exp["target_shard"])
    base_share = sum(r["base_dst_shard"] == cfg.exp["target_shard"] for r in rows) / len(rows)
    audit["control"] = dict(probability=info["control_probability"], seed=info["control_seed"],
        controlled_count=sum(r["control_bit"] for r in rows),
        source_reassigned_count=sum(r["source_reassigned"] for r in rows),
        destination_changed_count=sum(r["destination_changed"] for r in rows),
        base_target_destination_share=base_share,
        realized_target_destination_share=audit["target_in_count"]/len(rows),
        expected_target_destination_share=info["control_probability"]+(1-info["control_probability"])*base_share)
    audit.update(selection=info, source=source_manifest, workload_sha256=sha256_file(path))
    write_json(directory / "audit.json", audit)
    write_json(directory / "mapping_policy.json", info)
    return dict(name=name, path=str(path.resolve()), sha256=audit["workload_sha256"], audit=audit)


def validate_controlled(payload, cfg):
    for key, value in (("schema", 3), ("num_shards", cfg.chain.num_shards),
                       ("num_users", cfg.scale.num_users), ("user_base_index", cfg.scale.user_base_index)):
        if payload.get(key) != value:
            raise ValueError(f"Controlled payload incompatible {key}")
    info = payload["scenario"]
    if info.get("mapping_kind") != KIND:
        raise ValueError("Explicit synthetic mapping kind required")
    p, seed, target = info["control_probability"], info["control_seed"], info["target_shard"]
    validate_settings([p], seed)
    if (type(target) is not int or not 0 <= target < 16 or target != cfg.exp["target_shard"]
            or seed != cfg.exp["control_seed"] or p not in cfg.exp["control_probabilities"]):
        raise ValueError("Controlled policy/config mismatch")
    rows = payload["rows"]
    if len(rows) != cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"]):
        raise ValueError("Controlled workload count mismatch")
    ensure_order(rows)
    for i, r in enumerate(rows):
        if r["arrival_pos"] != i or r["sender_idx"] == r["receiver_idx"]:
            raise ValueError("Invalid controlled arrival/account identity")
        for address, user in ((r["orig_from"], r["sender_idx"]), (r["orig_to"], r["receiver_idx"])):
            n = int(address, 16)
            if not 0 <= n < 2**160 or type(user) is not int or user != cfg.scale.user_base_index + n % cfg.scale.num_users:
                raise ValueError("Invalid controlled original address/account")
        expected = route_fields(r, p, seed, target)
        if any(type(r[k]) is not type(v) or r[k] != v for k, v in expected.items()):
            raise ValueError("Controlled bit/route/provenance mismatch")
        if r["src_shard"] == r["dst_shard"]:
            raise ValueError("Controlled CTX cannot be intra-shard")
        if type(r["amount_wei"]) is not int or not int(cfg.traffic.value_floor_eth*ETH) <= r["amount_wei"] <= int(cfg.traffic.value_cap_eth*ETH):
            raise ValueError("Controlled amount outside range")
    return rows, sum(r["amount_wei"] for r in rows)
