"""Auditable directional workloads for experiments 14/15/16.

Original endpoints, integer wei, transaction identity and temporal order survive.
Only static address placement or explicit subset selection may change.
"""
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from .config import ETH
from .mapped_workload import candidate_pool, flow_stats, sha256_file, write_json


def snapshot_source(source, directory):
    """One immutable full CSV copy shared by the three experiments; never edit source.

    An interrupted .partial or missing manifest is rejected, never trusted/reused.
    """
    source, directory = Path(source).resolve(), Path(directory).resolve()
    target = directory / source.name
    if source == target:
        raise ValueError("Source snapshot cannot overwrite the input CSV")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "manifest.json"
    before = source.stat()
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (data["source"] != str(source) or data["source_size"] != before.st_size
                or data["source_mtime_ns"] != before.st_mtime_ns
                or not target.is_file() or sha256_file(target) != data["sha256"]):
            raise ValueError("Snapshot/source changed: choose a new snapshot directory; no files overwritten")
        return target, data
    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists() or partial.exists():
        raise ValueError("Incomplete snapshot exists; choose a new snapshot directory")
    h = hashlib.sha256()
    with source.open("rb") as inp, partial.open("xb") as out:
        for chunk in iter(lambda: inp.read(8 * 1024 * 1024), b""):
            h.update(chunk)
            out.write(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while copying; snapshot not committed")
    if sha256_file(partial) != h.hexdigest():
        raise ValueError("Snapshot copy hash mismatch")
    partial.rename(target)
    data = dict(source=str(source), source_size=before.st_size,
                source_mtime_ns=before.st_mtime_ns, snapshot=str(target), sha256=h.hexdigest())
    write_json(manifest, data)
    return target, data


def normalized(address):
    return f"0x{int(address, 16):040x}"


def shard_for(address, mapping):
    return mapping.get(normalized(address), int(address, 16) % 16)


def mapped(rows, mapping=None):
    mapping = mapping or {}
    return [{**r, "src_shard": shard_for(r["orig_from"], mapping),
             "dst_shard": shard_for(r["orig_to"], mapping)} for r in rows]


def cross_only(rows):
    return [r for r in rows if r["src_shard"] != r["dst_shard"]]


def ensure_order(rows):
    ids = [r["ctx_id"] for r in rows]
    lines = [r["source_line"] for r in rows]
    blocks = [int(r["source_block"]) for r in rows]
    if len(set(ids)) != len(ids) or any(a >= b for a, b in zip(lines, lines[1:])):
        raise ValueError("Duplicate IDs or non-increasing source lines")
    if any(a > b for a, b in zip(blocks, blocks[1:])):
        raise ValueError("CSV block order is not chronological; do not silently sort")


def direction_audit(rows, cfg, target):
    stats = flow_stats(rows, cfg)
    p = stats["per_shard"][target]
    incoming, outgoing = p["user_received_wei"], p["user_sent_wei"]
    step = max(1, int(cfg.exp["rate"] * cfg.exp["audit_window_blocks"]))
    windows = []
    for start in range(0, len(rows), step):
        subset = rows[start:start + step]
        net = sum(r["amount_wei"] * ((r["dst_shard"] == target) - (r["src_shard"] == target)) for r in subset)
        windows.append(dict(start=start, end=start + len(subset) - 1, n=len(subset), net_in_wei=net))
    signs = [(w["net_in_wei"] > 0) - (w["net_in_wei"] < 0) for w in windows]
    longest = run = 0
    for sign in signs:
        run = run + 1 if sign == 1 else 0
        longest = max(longest, run)
    total = stats["total_wei"]
    return dict(target_shard=target, flow=stats,
                target_direction_ratio=(incoming-outgoing)/(incoming+outgoing) if incoming+outgoing else 0,
                target_net_fraction=(incoming-outgoing)/total if total else 0,
                target_in_count=p["dst_count"], target_out_count=p["src_count"],
                target_in_wei=incoming, target_out_wei=outgoing,
                positive_window_fraction=sum(s == 1 for s in signs)/len(signs) if signs else 0,
                longest_positive_run=longest, windows=windows,
                time_basis="arrival windows: rate * audit_window_blocks CTX, not original wall-clock windows",
                source_first_block=rows[0]["source_block"], source_last_block=rows[-1]["source_block"],
                source_first_timestamp=rows[0]["source_timestamp"], source_last_timestamp=rows[-1]["source_timestamp"],
                amount_above_initial_balance_count=sum(r["amount_wei"] > int(cfg.exp["balances_eth"]*ETH) for r in rows))


def natural_windows(pool, cfg, count):
    """Nonoverlapping consecutive CTX windows, ranked on workload (not outcomes).

    Select first ordinary window plus low/high persistence-adjusted net-in windows.
    Candidate catalog and selection bias are exposed, no claim of holdout.
    """
    cross = cross_only(mapped(pool))
    chunks = [cross[i:i+count] for i in range(0, len(cross)-count+1, count)]
    if len(chunks) < 3:
        raise ValueError("exp14 needs >=3 full natural CTX windows; increase candidate_count")
    catalog = []
    for i, chunk in enumerate(chunks):
        a = direction_audit(chunk, cfg, cfg.exp["target_shard"])
        catalog.append(dict(index=i, first_line=chunk[0]["source_line"], last_line=chunk[-1]["source_line"],
                            direction_ratio=a["target_direction_ratio"], net_fraction=a["target_net_fraction"],
                            persistence=a["positive_window_fraction"],
                            score=max(0, a["target_net_fraction"])*a["positive_window_fraction"]))
    remaining = catalog[1:]
    strong = max(remaining, key=lambda x: (x["score"], -x["index"]))
    weak = min((x for x in remaining if x is not strong), key=lambda x: (abs(x["net_fraction"]), x["index"]))
    selected = [("ordinary", catalog[0]), ("low_direction", weak), ("high_direction", strong)]
    return [(name, chunks[item["index"]], {}, dict(window_index=item["index"],
             selection="exploratory same-pool selection; not held-out", requested_direction_met=item["score"] > 0))
            for name, item in selected], dict(window_catalog=catalog)


def learned_mapping(train, cfg, hotspots):
    accounts = defaultdict(lambda: [0, 0, 0])  # received, sent, observations
    for r in train:
        a, b, v = normalized(r["orig_from"]), normalized(r["orig_to"]), r["amount_wei"]
        accounts[a][1] += v
        accounts[b][0] += v
        accounts[a][2] += 1
        accounts[b][2] += 1
    candidates = []
    for addr, (received, sent, n) in accounts.items():
        score = (received-sent)/(received+sent) if received+sent else 0
        if n >= cfg.exp["min_address_observations"] and score >= cfg.exp["receiver_score_min"] and received > sent:
            candidates.append((addr, received-sent, score, n))
    candidates.sort(key=lambda x: (-x[1], x[0]))
    limit = max(1, int(len(accounts) * cfg.exp["max_receiver_address_fraction"]))
    chosen = candidates[:limit]
    if not chosen:
        raise ValueError("No qualifying receiving addresses in training data; change training parameters explicitly")
    # Static assignment by rank; no changes based on test data or transaction role.
    mapping = {addr: hotspots[i % len(hotspots)] for i, (addr, _, _, _) in enumerate(chosen)}
    return mapping, dict(training_addresses=len(accounts), selected_receiver_addresses=len(chosen),
                         receiver_catalog=[dict(address=a, net_in_wei=v, score=q, observations=n) for a,v,q,n in chosen])


def learned_layouts(pool, cfg, count):
    split = int(cfg.exp["train_candidates"])
    # Drop the rest of the split block from test: strict future-block evaluation.
    train = pool[:split]
    if not train or split >= len(pool):
        raise ValueError("Need both training and validation candidates")
    boundary = int(train[-1]["source_block"])
    test = [r for r in pool[split:] if int(r["source_block"]) > boundary]
    layouts, training = [], {}
    target = cfg.exp["target_shard"]
    for name, hot in [("hash_baseline", []), ("receiver_one", [target]),
                      ("receiver_two", [target, (target+1) % 16])]:
        mapping, info = learned_mapping(train, cfg, hot) if hot else ({}, {})
        rows = cross_only(mapped(test, mapping))[:count]
        if len(rows) != count:
            raise ValueError(f"{name}: insufficient held-out CTX; increase candidate_count")
        layouts.append((name, rows, mapping, dict(training_last_block=boundary,
                       test_first_block=rows[0]["source_block"], design="static training-derived receiver placement")))
        training[name] = info
    return layouts, dict(training=training, train_count=len(train), validation_candidates=len(test),
                         discarded_boundary_block_rows=len(pool)-split-len(test),
                         caveat="Layouts share validation candidate pool, not identical CTX after intra-shard filtering")


def stratified_sample(rows, count, target, p_in, background_fraction):
    """Exact count quotas within fixed amount bins; no value editing or replacement.

    In/out ratios apply only to target-touching CTX. Each scenario shares the same
    count histogram by original value bin. Achieved amount ratios are audited.
    """
    edges = (0, ETH//10, ETH//2, ETH, 2*ETH, 5*ETH, 10**50)
    groups = defaultdict(list)
    bins = [0]*(len(edges)-1)
    def bin_of(v):
        return next(i for i in range(len(edges)-1) if edges[i] <= v < edges[i+1])
    for r in rows[:count]:
        bins[bin_of(r["amount_wei"])] += 1
    if sum(bins) != count:
        raise ValueError("Insufficient baseline rows")
    for r in rows:
        direction = "in" if r["dst_shard"] == target else "out" if r["src_shard"] == target else "background"
        groups[(bin_of(r["amount_wei"]), direction)].append(r)
    selected, quotas = [], []
    for i, n in enumerate(bins):
        bg = round(n*background_fraction)
        incoming = round((n-bg)*p_in)
        quota = {"background": bg, "in": incoming, "out": n-bg-incoming}
        for direction, wanted in quota.items():
            available = groups[(i, direction)]
            if len(available) < wanted:
                raise ValueError(f"Insufficient {direction} transactions in amount bin {i}: {len(available)} < {wanted}; increase candidate_count, no duplication allowed")
            selected.extend(available[:wanted])
        quotas.append(dict(bin=i, lower_wei=edges[i], upper_wei_exclusive=edges[i+1], **quota))
    selected.sort(key=lambda r: r["source_line"])
    return selected, quotas


def sampled_layouts(pool, cfg, count):
    rows = cross_only(mapped(pool))
    result = [("unfiltered", rows[:count], {}, dict(design="ordinary cross-shard prefix"))]
    for p in cfg.exp["direction_ratios"]:
        selected, quotas = stratified_sample(rows, count, cfg.exp["target_shard"], p, cfg.exp["background_fraction"])
        result.append((f"inflow_{round(p*100):02d}", selected, {}, dict(
            requested_in_count_fraction=p, background_fraction=cfg.exp["background_fraction"],
            amount_bin_quotas=quotas, design="controlled subset; original ordering, endpoints and amounts preserved")))
    return result, dict(caveat="Inflow ratios are count quotas, not guaranteed ETH ratios; compare measured amount flows and persistence")


def export_scenario(directory, name, rows, mapping, info, cfg, source_manifest):
    directory.mkdir(parents=True, exist_ok=False)
    rows = [{**r, "arrival_pos": i} for i,r in enumerate(rows)]
    ensure_order(rows)
    audit = direction_audit(rows, cfg, cfg.exp["target_shard"])
    payload = dict(schema=2, num_shards=16, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   scenario=dict(name=name, mapping_kind="fixed_address_table"),
                   mapping=mapping, rows=rows)
    path = directory / "workload.json"
    write_json(path, payload)
    # Flat machine-readable derived copy: integer wei stays decimal text in CSV.
    with (directory / "transactions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    audit.update(selection=info, source=source_manifest, workload_sha256=sha256_file(path))
    write_json(directory / "audit.json", audit)
    write_json(directory / "address_mapping.json", mapping)
    return dict(name=name, path=str(path.resolve()), sha256=audit["workload_sha256"], audit=audit)


def validate_prepared(payload, cfg):
    """Validate new schema independently; no role-dependent placement permitted."""
    for key, value in (("schema", 2), ("num_shards", cfg.chain.num_shards),
                       ("num_users", cfg.scale.num_users), ("user_base_index", cfg.scale.user_base_index)):
        if payload.get(key) != value:
            raise ValueError(f"Directional payload incompatible {key}")
    mapping = payload["mapping"]
    for address, shard in mapping.items():
        if address != normalized(address) or type(shard) is not int or not 0 <= shard < 16:
            raise ValueError("Invalid static mapping")
    rows = payload["rows"]
    if len(rows) != cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"]):
        raise ValueError("Directional workload count mismatch")
    ensure_order(rows)
    for i, r in enumerate(rows):
        if r["arrival_pos"] != i or r["src_shard"] == r["dst_shard"] or r["sender_idx"] == r["receiver_idx"]:
            raise ValueError("Invalid arrival position or cross-shard identity")
        for address, shard, user in ((r["orig_from"], r["src_shard"], r["sender_idx"]), (r["orig_to"], r["dst_shard"], r["receiver_idx"])):
            if not 0 <= int(address,16) < 2**160 or shard != shard_for(address, mapping) or user != cfg.scale.user_base_index + int(address,16) % cfg.scale.num_users:
                raise ValueError("Directional mapping or simulated account mismatch")
        if type(r["amount_wei"]) is not int or not int(cfg.traffic.value_floor_eth*ETH) <= r["amount_wei"] <= int(cfg.traffic.value_cap_eth*ETH):
            raise ValueError("Directional amount outside range")
    return rows, sum(r["amount_wei"] for r in rows)
