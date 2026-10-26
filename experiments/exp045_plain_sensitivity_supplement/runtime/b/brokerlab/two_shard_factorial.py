"""Exp018: address-suffix-selected two-shard factorial workload.

Hotspot membership is ranked only by the low bits of the two original
addresses.  A separate seeded draw controls A->B versus B->A.  Logical
addresses make the final low nibble agree with every simulated shard.  This is
a synthetic transaction-level aliasing experiment, not static placement of the
original Ethereum identities.
"""
import csv
import hashlib
import math
from pathlib import Path

from .config import ETH
from .directional_workload import cross_only, ensure_order, mapped, normalized
from .mapped_workload import flow_stats, sha256_file, write_json

KIND = "address_suffix_two_shard_factorial_v1"
SCHEMA = 4
AMOUNT_EDGES = (0, ETH // 10, ETH // 2, ETH, 2 * ETH, 5 * ETH, 10**50)


def validate_settings(fractions, ratios, shard_a, shard_b, seed, suffix_bits):
    def probabilities(name, values, *, allow_one):
        upper = (lambda p: p <= 1) if allow_one else (lambda p: p < 1)
        if (not isinstance(values, list) or not values
                or any(type(p) not in (int, float) or not math.isfinite(p)
                       or p <= 0 or not upper(p) for p in values)
                or len(set(float(p) for p in values)) != len(values)):
            interval = "(0,1]" if allow_one else "(0,1)"
            raise ValueError(f"{name} must be distinct finite probabilities in {interval}")
    probabilities("hotspot_fractions", fractions, allow_one=True)
    probabilities("direction_ratios", ratios, allow_one=False)
    if (type(shard_a) is not int or type(shard_b) is not int
            or not 0 <= shard_a < 16 or not 0 <= shard_b < 16 or shard_a == shard_b):
        raise ValueError("hotspot shards must be distinct integers in 0..15")
    if type(seed) is not int or seed < 0:
        raise ValueError("direction_seed must be a nonnegative integer")
    if type(suffix_bits) is not int or not 4 <= suffix_bits <= 64:
        raise ValueError("hot_selector_bits_per_address must be an integer in 4..64")


def _interleave(a, b, bits):
    """Morton code made only from the low ``bits`` of each address."""
    out = 0
    for i in range(bits):
        out |= ((a >> i) & 1) << (2 * i)
        out |= ((b >> i) & 1) << (2 * i + 1)
    return out


def suffix_code(row, bits):
    mask = (1 << bits) - 1
    return _interleave(int(row["orig_from"], 16) & mask,
                       int(row["orig_to"], 16) & mask, bits)


def direction_draw(row, seed):
    key = f"exp018-direction-v1|{seed}|{row['ctx_id']}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def logical_address(address, shard):
    value = (int(address, 16) & ~0xF) | int(shard)
    return f"0x{value:040x}"


def cold_shard(address, excluded, start=0):
    """Choose a non-hot shard from successive address nibbles."""
    value = int(address, 16)
    for offset in range(40):
        shard = (value >> (4 * ((start + offset) % 40))) & 0xF
        if shard not in excluded:
            return shard
    # Degenerate address containing only excluded nibbles: use all suffix bits
    # deterministically, never randomness or experimental outcomes.
    cold = [s for s in range(16) if s not in excluded]
    return cold[value % len(cold)]


def background_route(row, shard_a, shard_b):
    excluded = {shard_a, shard_b}
    src = cold_shard(row["orig_from"], excluded)
    dst = cold_shard(row["orig_to"], excluded)
    if dst == src:
        for start in range(1, 41):
            dst = cold_shard(row["orig_to"], excluded, start)
            if dst != src:
                break
        else:
            cold = [s for s in range(16) if s not in excluded and s != src]
            dst = cold[int(row["orig_to"], 16) % len(cold)]
    return src, dst


def _common_rows(pool, count):
    rows = cross_only(mapped(pool))[:count]
    if len(rows) != count:
        raise ValueError("Insufficient common natural cross-shard CTX; increase candidate_count")
    ensure_order(rows)
    return rows


def _rankings(rows, cfg):
    bits = cfg.exp["hot_selector_bits_per_address"]
    seed = cfg.exp["direction_seed"]
    hot = sorted(range(len(rows)), key=lambda i: (suffix_code(rows[i], bits), rows[i]["ctx_id"]))
    draws = {i: direction_draw(rows[i], seed) for i in range(len(rows))}
    return hot, draws


def _route_rows(rows, cfg, kind, alpha=None, beta=None):
    a, b = cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"]
    bits, seed = cfg.exp["hot_selector_bits_per_address"], cfg.exp["direction_seed"]
    hot_order, draws = _rankings(rows, cfg)
    selected = set()
    a_to_b = set()
    if kind == "factorial":
        k = round(float(alpha) * len(rows))
        selected = set(hot_order[:k])
        direction_order = sorted(selected, key=lambda i: (draws[i], rows[i]["ctx_id"]))
        a_to_b = set(direction_order[:round(float(beta) * k)])
    result = []
    for i, row in enumerate(rows):
        base_src = int(row["orig_from"], 16) & 0xF
        base_dst = int(row["orig_to"], 16) & 0xF
        if kind == "natural":
            src, dst, hot_bit, direction_bit = base_src, base_dst, 0, -1
        elif kind == "isolated":
            src, dst = background_route(row, a, b)
            hot_bit, direction_bit = 0, -1
        elif i in selected:
            hot_bit = 1
            direction_bit = int(i in a_to_b)
            src, dst = (a, b) if direction_bit else (b, a)
        else:
            src, dst = background_route(row, a, b)
            hot_bit, direction_bit = 0, -1
        result.append({**row,
            "base_src_shard": base_src, "base_dst_shard": base_dst,
            "hot_code_hex": format(suffix_code(row, bits), f"0{(2*bits+3)//4}x"),
            "hotspot_bit": hot_bit,
            "direction_draw_hex": f"{draws[i]:016x}",
            "direction_bit": direction_bit,
            "src_shard": src, "dst_shard": dst,
            "logical_from": logical_address(row["orig_from"], src),
            "logical_to": logical_address(row["orig_to"], dst)})
    return result


def _name_value(prefix, value):
    text = format(float(value), ".12f").rstrip("0").rstrip(".")
    return prefix + text.replace(".", "_")


def two_shard_layouts(pool, cfg, count):
    rows = _common_rows(pool, count)
    a, b = cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"]
    layouts = []
    for name, kind in (("natural_suffix_baseline", "natural"),
                       ("isolated_background_alpha0", "isolated")):
        info = dict(mapping_kind=KIND, scenario_kind=kind, hotspot_shard_a=a,
                    hotspot_shard_b=b, hotspot_fraction=0.0, direction_ratio=None,
                    direction_seed=cfg.exp["direction_seed"],
                    hot_selector_bits_per_address=cfg.exp["hot_selector_bits_per_address"])
        layouts.append((name, _route_rows(rows, cfg, kind), {}, info))
    for alpha in cfg.exp["hotspot_fractions"]:
        for beta in cfg.exp["direction_ratios"]:
            name = _name_value("hot_", alpha) + "_" + _name_value("dir_", beta)
            info = dict(mapping_kind=KIND, scenario_kind="factorial", hotspot_shard_a=a,
                        hotspot_shard_b=b, hotspot_fraction=float(alpha), direction_ratio=float(beta),
                        direction_seed=cfg.exp["direction_seed"],
                        hot_selector_bits_per_address=cfg.exp["hot_selector_bits_per_address"])
            layouts.append((name, _route_rows(rows, cfg, "factorial", alpha, beta), {}, info))
    ids_hash = hashlib.sha256("\n".join(r["ctx_id"] for r in rows).encode()).hexdigest()
    return layouts, dict(mapping_kind=KIND, common_ctx_count=count, paired_rows_sha256=ids_hash,
        hotspot_fractions=cfg.exp["hotspot_fractions"], direction_ratios=cfg.exp["direction_ratios"],
        hotspot_shards=[a, b], selection="Morton rank of low address bits; no random hotspot draw",
        direction="Independent seeded SHA256 draw; exact count quota",
        caveat="Logical per-transaction aliases are synthetic; original Ethereum addresses are provenance only")


def _two_shard_audit(rows, cfg, info):
    a, b = info["hotspot_shard_a"], info["hotspot_shard_b"]
    hot = [r for r in rows if r["hotspot_bit"]]
    ab = [r for r in hot if r["src_shard"] == a and r["dst_shard"] == b]
    ba = [r for r in hot if r["src_shard"] == b and r["dst_shard"] == a]
    total_wei = sum(r["amount_wei"] for r in rows)
    hot_wei = sum(r["amount_wei"] for r in hot)
    ab_wei = sum(r["amount_wei"] for r in ab)
    ba_wei = sum(r["amount_wei"] for r in ba)
    hotspot_endpoints = sum((r["src_shard"] in (a, b)) + (r["dst_shard"] in (a, b)) for r in rows)
    pair_transactions = sum({r["src_shard"], r["dst_shard"]} == {a, b} for r in rows)
    step = max(1, int(cfg.exp["rate"] * cfg.exp["audit_window_blocks"]))
    windows = []
    for start in range(0, len(rows), step):
        part = rows[start:start+step]
        ph = [r for r in part if r["hotspot_bit"]]
        pab = [r for r in ph if r["direction_bit"] == 1]
        windows.append(dict(start=start, n=len(part), hotspot_count=len(ph),
            hotspot_fraction=len(ph)/len(part), a_to_b_count=len(pab),
            a_to_b_ratio=len(pab)/len(ph) if ph else None,
            net_count_pressure=(2*len(pab)-len(ph))/len(part)))
    return dict(configured_hotspot_fraction=info["hotspot_fraction"],
        configured_a_to_b_ratio=info["direction_ratio"], hotspot_count=len(hot),
        realized_hotspot_count_fraction=len(hot)/len(rows),
        actual_hotspot_endpoint_count=hotspot_endpoints,
        actual_hotspot_endpoint_fraction=hotspot_endpoints/(2*len(rows)),
        actual_a_b_pair_transaction_count=pair_transactions,
        actual_a_b_pair_transaction_fraction=pair_transactions/len(rows),
        realized_hotspot_amount_fraction=hot_wei/total_wei if total_wei else 0,
        a_to_b_count=len(ab), b_to_a_count=len(ba), background_count=len(rows)-len(hot),
        realized_a_to_b_count_ratio=len(ab)/len(hot) if hot else None,
        realized_a_to_b_amount_ratio=ab_wei/hot_wei if hot_wei else None,
        a_to_b_wei=ab_wei, b_to_a_wei=ba_wei,
        net_count_pressure=(len(ab)-len(ba))/len(rows),
        theoretical_net_count_pressure=(float(info["hotspot_fraction"])*(2*float(info["direction_ratio"])-1)
                                        if info["direction_ratio"] is not None else None),
        net_amount_pressure=(ab_wei-ba_wei)/total_wei if total_wei else 0,
        per_window=windows)


def export_two_shard(directory, name, rows, mapping, info, cfg, source_manifest):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    rows = [{**r, "arrival_pos": i} for i, r in enumerate(rows)]
    payload = dict(schema=SCHEMA, num_shards=16, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   scenario={"name": name, **info}, rows=rows)
    validate_two_shard(payload, cfg)
    path = directory / "workload.json"
    write_json(path, payload)
    with (directory / "transactions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    audit = dict(flow=flow_stats(rows, cfg), hotspot_control=_two_shard_audit(rows, cfg, info),
        source_first_block=rows[0]["source_block"], source_last_block=rows[-1]["source_block"],
        source_first_timestamp=rows[0]["source_timestamp"], source_last_timestamp=rows[-1]["source_timestamp"],
        amount_above_initial_balance_count=sum(r["amount_wei"] > int(cfg.exp["balances_eth"]*ETH) for r in rows),
        selection=info, source=source_manifest, workload_sha256=sha256_file(path))
    write_json(directory / "audit.json", audit)
    write_json(directory / "mapping_policy.json", info)
    return dict(name=name, path=str(path.resolve()), sha256=audit["workload_sha256"], audit=audit)


def validate_two_shard(payload, cfg):
    for key, value in (("schema", SCHEMA), ("num_shards", cfg.chain.num_shards),
                       ("num_users", cfg.scale.num_users), ("user_base_index", cfg.scale.user_base_index)):
        if payload.get(key) != value:
            raise ValueError(f"Two-shard payload incompatible {key}")
    info = payload.get("scenario", {})
    if info.get("mapping_kind") != KIND:
        raise ValueError("Explicit two-shard mapping kind required")
    validate_settings(cfg.exp["hotspot_fractions"], cfg.exp["direction_ratios"],
        cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"],
        cfg.exp["direction_seed"], cfg.exp["hot_selector_bits_per_address"])
    for key in ("hotspot_shard_a", "hotspot_shard_b", "direction_seed", "hot_selector_bits_per_address"):
        if info.get(key) != cfg.exp[key]:
            raise ValueError("Two-shard policy/config mismatch")
    kind = info.get("scenario_kind")
    if kind not in ("natural", "isolated", "factorial"):
        raise ValueError("Invalid two-shard scenario kind")
    if kind == "factorial":
        if (info.get("hotspot_fraction") not in [float(x) for x in cfg.exp["hotspot_fractions"]]
                or info.get("direction_ratio") not in [float(x) for x in cfg.exp["direction_ratios"]]):
            raise ValueError("Two-shard factor/config mismatch")
    elif info.get("hotspot_fraction") != 0.0 or info.get("direction_ratio") is not None:
        raise ValueError("Invalid baseline factors")
    rows = payload.get("rows", [])
    if len(rows) != cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"]):
        raise ValueError("Two-shard workload count mismatch")
    ensure_order(rows)
    base = [{k: v for k, v in r.items() if k not in {
        "base_src_shard", "base_dst_shard", "hot_code_hex", "hotspot_bit",
        "direction_draw_hex", "direction_bit", "src_shard", "dst_shard",
        "logical_from", "logical_to", "arrival_pos"}} for r in rows]
    expected = _route_rows(base, cfg, kind, info.get("hotspot_fraction"), info.get("direction_ratio"))
    checked = ("base_src_shard", "base_dst_shard", "hot_code_hex", "hotspot_bit",
               "direction_draw_hex", "direction_bit", "src_shard", "dst_shard",
               "logical_from", "logical_to")
    ids = set()
    for i, (row, exp) in enumerate(zip(rows, expected)):
        if (row.get("arrival_pos") != i or row["ctx_id"] in ids or row["sender_idx"] == row["receiver_idx"]
                or row["src_shard"] == row["dst_shard"]):
            raise ValueError("Invalid two-shard identity/order/route")
        ids.add(row["ctx_id"])
        if any(type(row.get(k)) is not type(exp[k]) or row.get(k) != exp[k] for k in checked):
            raise ValueError("Two-shard suffix/direction/logical-address mismatch")
        if (int(row["logical_from"], 16) & 0xF) != row["src_shard"] or (int(row["logical_to"], 16) & 0xF) != row["dst_shard"]:
            raise ValueError("Logical address suffix does not map to route")
        for address, user in ((row["orig_from"], row["sender_idx"]), (row["orig_to"], row["receiver_idx"])):
            n = int(address, 16)
            if not 0 <= n < 2**160 or type(user) is not int or user != cfg.scale.user_base_index + n % cfg.scale.num_users:
                raise ValueError("Invalid original address/account provenance")
        if type(row["amount_wei"]) is not int or not int(cfg.traffic.value_floor_eth*ETH) <= row["amount_wei"] <= int(cfg.traffic.value_cap_eth*ETH):
            raise ValueError("Two-shard amount outside range")
    if kind != "natural" and any((r["src_shard"] in (cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"])
                                  or r["dst_shard"] in (cfg.exp["hotspot_shard_a"], cfg.exp["hotspot_shard_b"]))
                                 and not r["hotspot_bit"] for r in rows):
        raise ValueError("Background leaked into hotspot shards")
    return rows, sum(r["amount_wei"] for r in rows)
