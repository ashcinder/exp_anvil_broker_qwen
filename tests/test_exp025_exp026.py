"""Checks for whole-round sampling, measured-event exports and honest fee accounting."""
import json
from pathlib import Path

import pytest
import yaml

from brokerlab.config import load_config
from experiments.exp025_regime_shift_sensitivity import pipeline
from experiments.exp025_regime_shift_sensitivity.paper_data import export_profit, read_csv
from experiments.exp025_regime_shift_sensitivity.runner import tasks_for

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments" / "exp025_regime_shift_sensitivity"


def test_grid_is_50_points_times_10_whole_rounds():
    cfg = load_config(HERE / "config.yaml")
    space = yaml.safe_load((HERE / "search_space.yaml").read_text(encoding="utf-8"))
    tasks = tasks_for(cfg, "exp025", space)
    assert len(tasks) == 500
    assert len({t["id"] for t in tasks}) == 500
    assert space["epsilon"]["values"] == [i/20 for i in range(1,21)]
    assert cfg.scale.num_brokers * cfg.exp["ctx_per_broker"] == 40000
    assert space["q_min"]["values"] == [i/10 for i in range(1, 10)]
    assert space["half_life"]["values"] == [1,2,4,6,8,10,15,20,25,30,35,40,45,50,100,150,200,250,300,400,500]
    for rep in range(1, 11):
        group = [t for t in tasks if t["round"] == rep]
        assert len(group) == 50
        assert {t["route_seed"] for t in group} == {7 + (rep-1)*10}
    for name, spec in space.items():
        for value in spec["values"]:
            assert len([t for t in tasks if t["sweep"] == name and t["value"] == value]) == 10


def test_modern_move_joins_real_blocks_and_checks_correspondence():
    data = dict(moves=[(0,11,70)], move_events=[dict(src_shard=0,dst_shard=11,amount_wei=70,burn_block=5,mint_block=7)], transfers_done=1)
    assert pipeline.normalize_tdr_moves(4, data) == [(4,0,11,70,5,7,"done")]
    data["move_events"][0]["amount_wei"] = 71
    with pytest.raises(ValueError, match="disagree"):
        pipeline.normalize_tdr_moves(4, data)


def test_missing_event_blocks_are_rejected():
    with pytest.raises(ValueError, match="move_events missing"):
        pipeline.normalize_tdr_moves(0, dict(moves=[(0,1,70)], transfers_done=1))
    with pytest.raises(ValueError, match="no recorded burn block"):
        pipeline.normalize_tdr_moves(0, dict(lost_moves=[(0,1,70)]))


def test_communication_uses_receipt_measurements_not_route_multiplier():
    rows = [dict(ok=True,route="broker"), dict(ok=True,route="relay",t1_tx_bytes=120,t1_receipt_bytes=850)]
    pipeline.add_communication_bill(rows,100)
    assert [r["xmit_bytes"] for r in rows] == [100,970]
    with pytest.raises(ValueError, match="byte measurements"):
        pipeline.add_communication_bill([dict(ok=True,route="relay",t1_tx_bytes=120)],100)


def test_profit_cost_is_at_recorded_burn_not_uniformly_spread(tmp_path):
    rows = [dict(arrival_pos=i, t2_block=block, broker_idx=0, route="broker",fee_broker_wei=10)
            for i,block in enumerate((3,8,12))]
    moves = [dict(broker_idx=0,burn_block=9)]
    export_profit(rows,moves,tmp_path,0,7)
    result = read_csv(tmp_path/"profit_by_tx.csv")
    assert [int(r["modeled_tdr_cost_wei"]) for r in result] == [0,7,7]
    assert [int(r["net_profit_wei"]) for r in result] == [10,13,23]
    meta=json.loads((tmp_path/"profit_metadata.json").read_text())
    assert meta["actual_chain_tdr_gas_cost_wei"] == 0
    assert meta["fee_income_wei"]-meta["modeled_tdr_cost_wei"] == meta["net_profit_wei"]


def test_exp26_pairs_share_round_seed_and_workload_size():
    cfg=load_config(ROOT/"experiments/exp026_regime_shift_paper_figures/config.yaml")
    tasks=tasks_for(cfg,"exp026")
    assert len(tasks)==2
    assert cfg.b2e.enabled
    assert cfg.scale.num_brokers*cfg.exp["ctx_per_broker"]==80000
    for rep in range(1,2):
        pair=[t for t in tasks if t["round"]==rep]
        assert {t["token"].split("@")[0] for t in pair}=={"plain","topup"}
        assert len({t["route_seed"] for t in pair})==1
