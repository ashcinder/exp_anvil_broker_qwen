"""Exp018 tests require neither Anvil nor the full trace."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from brokerlab.config import ETH, load_config
from brokerlab.mapped_workload import load_prepared
from brokerlab import two_shard_factorial as ts

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/exp018_two_shard_factorial"


def config(count=100):
    cfg = load_config(HERE / "config.yaml")
    return replace(cfg, scale=replace(cfg.scale, num_brokers=1),
                   exp={**cfg.exp, "ctx_per_broker": count})


def pool(count=160):
    rows = []
    for i in range(count):
        src = 0x100000 + 16*i + 2
        dst = 0x900000 + 32*i + 3
        assert src % 200 != dst % 200
        rows.append(dict(ctx_id=f"tx{i}", orig_from=hex(src), orig_to=hex(dst),
            amount_wei=(i % 10 + 1) * ETH // 10,
            sender_idx=200 + src % 200, receiver_idx=200 + dst % 200,
            source_line=i+2, source_block=str(100+i//5), source_timestamp=str(i)))
    return rows


def test_factorial_counts_nesting_suffixes_and_background_isolation():
    cfg = config()
    original = pool()
    before = copy.deepcopy(original)
    layouts, design = ts.two_shard_layouts(original, cfg, 100)
    assert original == before
    assert len(layouts) == 26 and design["common_ctx_count"] == 100
    assert [r["ctx_id"] for _, rows, _, _ in layouts for r in rows[:1]] == ["tx0"] * 26
    natural = layouts[0][1]
    isolated = layouts[1][1]
    assert all(r["src_shard"] == 2 and r["dst_shard"] == 3 for r in natural)
    assert all(not ({r["src_shard"], r["dst_shard"]} & {0, 1}) for r in isolated)
    by_factor = {(info["hotspot_fraction"], info["direction_ratio"]): rows
                 for _, rows, _, info in layouts if info["scenario_kind"] == "factorial"}
    for alpha in cfg.exp["hotspot_fractions"]:
        previous = set()
        for beta in cfg.exp["direction_ratios"]:
            rows = by_factor[(alpha, beta)]
            hot = [r for r in rows if r["hotspot_bit"]]
            ab = {r["ctx_id"] for r in hot if r["direction_bit"] == 1}
            assert len(hot) == round(alpha * 100)
            assert len(ab) == round(beta * len(hot))
            assert previous <= ab
            previous = ab
            assert all((int(r["logical_from"], 16) & 15) == r["src_shard"] for r in rows)
            assert all((int(r["logical_to"], 16) & 15) == r["dst_shard"] for r in rows)
            assert all(not ({r["src_shard"], r["dst_shard"]} & {0, 1})
                       for r in rows if not r["hotspot_bit"])
    previous = set()
    for alpha in cfg.exp["hotspot_fractions"]:
        hot = {r["ctx_id"] for r in by_factor[(alpha, 0.5)] if r["hotspot_bit"]}
        assert previous <= hot
        previous = hot


def test_direction_draw_is_independent_of_amount_and_route_seed():
    cfg = config()
    row = pool(1)[0]
    draw = ts.direction_draw(row, cfg.exp["direction_seed"])
    assert draw == ts.direction_draw({**row, "amount_wei": 5*ETH}, cfg.exp["direction_seed"])
    cfg.exp["route_seed"] = 999
    assert draw == ts.direction_draw(row, cfg.exp["direction_seed"])
    assert draw != ts.direction_draw(row, cfg.exp["direction_seed"] + 1)


def frozen(tmp_path, alpha=0.5, beta=0.7):
    cfg = config()
    layouts, _ = ts.two_shard_layouts(pool(), cfg, 100)
    name, rows, mapping, info = next(x for x in layouts
        if x[3]["hotspot_fraction"] == alpha and x[3]["direction_ratio"] == beta)
    item = ts.export_two_shard(tmp_path/name, name, rows, mapping, info, cfg, {})
    payload = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
    return cfg, item, payload


def test_schema4_roundtrip_and_exact_audit(tmp_path):
    cfg, item, payload = frozen(tmp_path)
    rows, total = load_prepared(replace(cfg, exp={**cfg.exp,
        "prepared_workload_path": item["path"], "prepared_workload_sha256": item["sha256"]}))
    assert len(rows) == 100 and total == sum(r["amount_wei"] for r in rows)
    audit = item["audit"]["hotspot_control"]
    assert audit["hotspot_count"] == 50
    assert audit["a_to_b_count"] == 35
    assert audit["b_to_a_count"] == 15
    assert audit["realized_hotspot_count_fraction"] == 0.5
    assert audit["actual_hotspot_endpoint_fraction"] == 0.5
    assert audit["actual_a_b_pair_transaction_fraction"] == 0.5
    assert audit["realized_a_to_b_count_ratio"] == 0.7
    assert payload["schema"] == 4


@pytest.mark.parametrize("field", ["hot_code_hex", "hotspot_bit", "direction_draw_hex",
    "direction_bit", "src_shard", "dst_shard", "logical_from", "logical_to",
    "base_src_shard", "amount_wei", "sender_idx", "arrival_pos", "ctx_id"])
def test_schema4_rejects_tampering(tmp_path, field):
    cfg, _, payload = frozen(tmp_path)
    row = payload["rows"][0]
    if field in ("hot_code_hex", "direction_draw_hex"):
        row[field] = "tampered"
    elif field in ("logical_from", "logical_to"):
        row[field] = "0x" + "f"*40
    elif field == "amount_wei":
        row[field] = 0
    elif field == "ctx_id":
        payload["rows"][1][field] = row[field]
    else:
        row[field] += 1
    with pytest.raises(ValueError):
        ts.validate_two_shard(payload, cfg)


@pytest.mark.parametrize("fractions,ratios,a,b,seed,bits", [
    ([], [.5], 0, 1, 1, 32), ([0], [.5], 0, 1, 1, 32), ([1.1], [.5], 0, 1, 1, 32),
    ([.5], [], 0, 1, 1, 32), ([.5], [0], 0, 1, 1, 32), ([.5], [1], 0, 1, 1, 32),
    ([.5], [.5], 0, 0, 1, 32), ([.5], [.5], -1, 1, 1, 32),
    ([.5], [.5], 0, 1, -1, 32), ([.5], [.5], 0, 1, 1, 3),
])
def test_invalid_settings(fractions, ratios, a, b, seed, bits):
    with pytest.raises(ValueError):
        ts.validate_settings(fractions, ratios, a, b, seed, bits)
