"""Bounded placement proofs, trace filtering, replay identity, and failed-run gates."""
import csv
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from brokerlab.config import ETH, apply_overrides, load_config
from brokerlab.mapped_workload import (candidate_pool, collect_blocks, load_prepared,
                                       placement, prepare_scenario, sha256_file, theoretical)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/exp012_mapping_congestion/run.py"
spec = importlib.util.spec_from_file_location("exp012_test_runner", PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def config():
    return load_config(PATH.parent / "config.yaml")


@pytest.mark.parametrize("bits,hot,buckets", [
    (4, [0], [1] * 16), (5, [0], [17] + [1] * 15),
    (6, [0], [49] + [1] * 15), (5, [0, 1], [9, 9] + [1] * 14),
    (6, [0, 1], [25, 25] + [1] * 14),
    (5, [0, 1, 2], [7, 6, 6] + [1] * 13),
    (6, [0, 1, 2], [17, 17, 17] + [1] * 13),
])
def test_exact_bucket_weights(bits, hot, buckets):
    info = theoretical({"bits": bits, "hotspots": hot})
    assert info["bucket_counts"] == buckets
    assert sum(info["address_shares"]) == 1
    assert sum(info["iid_ctx_endpoint_shares"]) == pytest.approx(1)
    for n in range(256):
        # Higher bits and checksum capitalization have no effect.
        assert placement(hex(n), bits, hot) == placement(hex(n + 2**120).upper(), bits, hot)
        if bits == 4:
            assert placement(hex(n), bits, hot) == n % 16


def test_symmetric_endpoints_do_not_invent_directional_flow():
    matrix = [[0] * 16 for _ in range(16)]
    for a in range(64):
        for b in range(64):
            s, d = placement(hex(a), 6, [0]), placement(hex(b), 6, [0])
            if s != d:
                matrix[s][d] += 1
    assert max(sum(r) for r in matrix) > min(sum(r) for r in matrix)
    assert all(matrix[s][d] == matrix[d][s] for s in range(16) for d in range(16))


@pytest.mark.parametrize("bits,hot", [(3, [0]), (5.5, [0]), (6, []), (6, [0, 0]), (6, [16]), (6, [0, 1, 2, 3, 4])])
def test_invalid_placement(bits, hot):
    with pytest.raises(ValueError):
        placement("0x123", bits, hot)


def test_common_pool_keeps_intra_and_contracts_then_filters_per_layout(tmp_path):
    path = tmp_path / "trace.csv"
    header = ["transactionHash", "from", "to", "value", "toCreate", "isError", "fromIsContract", "toIsContract"]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        # First is intra under base 4 bits, but cross under the 5-bit layout.
        w.writerow(["a", "0x01", "0x11", ETH // 10, "false", "false", "true", "true"])
        w.writerow(["b", "0x02", "0x13", ETH // 10, "false", "false", "false", "false"])
        w.writerow(["bad", "0x02", "0x13", ETH // 10, "false", "true", "false", "false"])
        w.writerow(["c", "0x03", "0x14", ETH // 10, "false", "false", "false", "false"])
    cfg = config()
    cfg = replace(cfg, traffic=replace(cfg.traffic, real_csv_path=str(path)))
    pool, reasons = candidate_pool(cfg, 3)
    assert reasons["creation_or_error"] == 1
    base_path, base = prepare_scenario(pool, {"name": "base", "bits": 4, "hotspots": [0]}, cfg, 2, tmp_path / "base")
    hot_path, hot = prepare_scenario(pool, {"name": "hot", "bits": 5, "hotspots": [0]}, cfg, 2, tmp_path / "hot")
    assert base["candidate_pool"]["intra_count"] == 1
    assert hot["candidate_pool"]["intra_count"] == 0
    assert [r["ctx_id"] for r in json.loads(base_path.read_text())["rows"]] == ["real_b", "real_c"]
    assert [r["ctx_id"] for r in json.loads(hot_path.read_text())["rows"]] == ["real_a", "real_b"]
    cfg = replace(cfg, scale=replace(cfg.scale, num_brokers=1), exp={**cfg.exp,
                  "ctx_per_broker": 2, "prepared_workload_path": str(hot_path),
                  "prepared_workload_sha256": sha256_file(hot_path)})
    rows, total = load_prepared(cfg)
    assert total == ETH // 5 and len(rows) == 2
    assert sum(p["user_net_in_wei"] for p in hot["selected_ctx"]["per_shard"]) == 0
    hot_path.write_text(hot_path.read_text() + " ")
    with pytest.raises(ValueError, match="SHA256"):
        load_prepared(cfg)
    with pytest.raises(ValueError, match="increase"):
        prepare_scenario(pool, {"name": "base", "bits": 4, "hotspots": [0]}, cfg, 3, tmp_path / "short")


def test_configuration_derives_all_funds_and_arms():
    cfg, plan = runner.resolve(config())
    assert plan["arm_runs"] == 45 and plan["total_ctx"] == 450000
    assert plan["valve_trigger_eth"] == 1.25
    cfg, plan = runner.resolve(apply_overrides(cfg, ["exp.balances_eth=0.3", "exp.valve_threshold=2"]))
    assert cfg.broker.initial_balance_eth == .3
    assert all(token.split("@")[1] == "0.3" for token in cfg.exp["arms"].split(","))
    assert plan["valve_trigger_eth"] == .6


def test_failed_or_missing_summary_never_becomes_valid(tmp_path):
    report = runner.aggregate([{"scenario": "uniform4", "session": 1, "route_seed": 7,
                                "returncode": 1, "summary": None}], tmp_path, 10000)
    assert not report["all_integrity_valid"]
    assert report["paired_ewma_comparisons"] == []


def test_capacity_scan_uses_common_mined_height(tmp_path):
    class Eth:
        block_number = 3

        def get_block(self, h):
            assert 2 <= h <= 3
            return {"gasUsed": 21000 if h == 2 else 0, "gasLimit": 21000, "transactions": [1] if h == 2 else []}

    conns = SimpleNamespace(web3=lambda s: SimpleNamespace(eth=Eth()))
    rows = [{"t1_block": 2, "t2_block": 4, "src": 0, "dst": 1, "t1_hops": 1, "t2_hops": 2}]
    report = collect_blocks(conns, rows, tmp_path, 2)
    assert report["end_block"] == 3 and report["last_ctx_block"] == 4
    assert report["max_full_block_fraction"] == .5
    assert report["per_shard"][1]["ctx_half_hops_gt1_fraction"] == 1
