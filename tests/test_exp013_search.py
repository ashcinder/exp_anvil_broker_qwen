"""Search semantics and end-to-end checkpoints, without requiring live nodes."""
import csv
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from brokerlab.config import ETH, load_config, to_params_dict
from brokerlab.mapped_workload import candidate_pool, sha256_file

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/exp013_parameter_search"
spec = importlib.util.spec_from_file_location("exp013_test_runner", HERE / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def cfg():
    return load_config(HERE / "config.yaml")


def result(candidate, relay=.1, cost=.02, throughput=100, delay=2):
    return {"candidate": candidate, "metrics": {"relay_fraction": relay, "tdr_legs_per_ctx": cost,
            "throughput_ctx_per_s": throughput, "e2e_p95_s": delay}}


def test_grid_size_and_no_ewma_window_product():
    c, plan = runner.resolve(cfg())
    full = runner.candidates(c.exp)
    assert len(full) == 177 and len(runner.candidates(c.exp, True)) == 45
    assert plan["cells"] == 63 and plan["coarse_trials"] == 2835
    assert plan["full_train_grid_trials"] == 11151
    assert plan["validation_trials_upper_bound"] == 819
    assert plan["ctx_per_trial"] == 10000
    assert plan["validation_windows"] == 1 and plan["validation_seeds"] == [7]
    assert (c.traffic.value_floor_eth, c.traffic.value_cap_eth) == (.05, 5.0)
    assert sum(x["method"] == "ewma" for x in full) == 56
    assert all("window" not in x for x in full if x["method"] == "ewma")
    assert c.exp["balances"] == [.1, .2, .3, .5, .7, 1., 1.5, 2., 5.]


@pytest.mark.parametrize("key,value", [("epsilons", [.1, 1]), ("epsilons", [.1, float("nan")]),
    ("windows", [0, 1]), ("balances", [.5, .5]), ("coarse_epsilons", [.123]),
    ("validation_seeds", [7, 7]), ("candidate_count", 100)])
def test_invalid_grid_rejected(key, value):
    c = cfg()
    c.exp[key] = value
    with pytest.raises(ValueError):
        runner.resolve(c)


def test_every_grid_level_has_an_exploration_path():
    c = cfg()
    for method in ("proportional", "topup", "ewma"):
        probes = [x for x in runner.candidates(c.exp, True) if x["method"] == method]
        probes += runner.coverage_probes(c.exp, method)
        assert {x["epsilon"] for x in probes} == set(c.exp["epsilons"])
        assert {x["memory"] for x in probes} == set(c.exp["half_lives" if method == "ewma" else "windows"])


def test_trial_encoding_overrides_all_relevant_parameters():
    base = cfg()
    for c in runner.candidates(base.exp):
        trial = runner.trial_config(base, c, .2, Path("x.json"), "hash", 17)
        assert trial.broker.initial_balance_eth == trial.exp["balances_eth"] == .2
        assert trial.exp["route_seed"] == 17
        assert trial.exp["arms"] == runner.arm(c, .2)[0]
        if c["method"] in ("proportional", "topup"):
            assert trial.exp["tdr_window_blocks"] == c["memory"]
        if "epsilon" in c:
            assert trial.exp["tdr_epsilon"] == trial.exp["tdr_surplus_epsilon"] == c["epsilon"]
        if c["method"] == "ewma":
            assert float(trial.exp["arms"].split("@")[5]) == c["memory"]


def test_pareto_does_not_select_by_relay_alone():
    a = result({"method": "topup", "epsilon": .1}, relay=.01, cost=.9)
    b = result({"method": "topup", "epsilon": .5}, relay=.1, cost=.01)
    bad = result({"method": "topup", "epsilon": .9}, relay=.2, cost=1, throughput=90, delay=3)
    layers = runner.pareto_layers([bad, a, b])
    assert {runner.identity(r["candidate"]) for r in layers[0]} == {runner.identity(a["candidate"]), runner.identity(b["candidate"])}
    assert layers[1] == [bad]
    assert runner.shortlist([bad, a, b], 2) == runner.shortlist([b, a, bad], 2)


def test_held_out_pool_deduplicates_training_prefix(tmp_path):
    path = tmp_path / "trace.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["transactionHash", "from", "to", "value", "isError", "toCreate"])
        for txid in ("a", "b", "a", "c", "d"):
            w.writerow([txid, "0x01", "0x02", ETH // 10, "false", "false"])
    c = cfg()
    c = replace(c, traffic=replace(c.traffic, real_csv_path=str(path)))
    train, _ = candidate_pool(c, 2)
    held, counters = candidate_pool(c, 2, skip=2)
    assert {r["ctx_id"] for r in train}.isdisjoint(r["ctx_id"] for r in held)
    assert [r["ctx_id"] for r in held] == ["real_c", "real_d"]
    assert counters["duplicate_hash"] == 1


def test_budget_resume_stage_freeze_and_validation_no_leakage(tmp_path, monkeypatch):
    c = cfg()
    c = replace(c, exp={**c.exp, "balances": [.5], "valve_thresholds": [1.1],
        "scenarios": [{"name": "three_hot6", "bits": 6, "hotspots": [0, 1, 2]}],
        "epsilons": [.1, .5], "windows": [1, 2], "half_lives": [1, 2],
        "coarse_epsilons": [.1], "coarse_windows": [1], "coarse_half_lives": [1],
        "shortlist_per_method": 1, "validation_windows": 2, "validation_seeds": [7, 17]})
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(to_params_dict(c)))
    monkeypatch.setattr(runner, "HERE", tmp_path)
    monkeypatch.setattr(runner, "sources", lambda: {"fixed": "hash"})
    datasets = []

    def data(cfg, output, split):
        datasets.append(split)
        return {}

    calls = []

    def execute(cfg, output, trial, stage, dataset):
        tid = runner.identity(trial)
        assert tid not in calls  # Completed trials must never execute twice.
        calls.append(tid)
        path = output / "trials" / tid / "fake_summary.json"
        runner.save(path, {"ok": True})
        r = {**trial, **result(trial["candidate"]), "trial_id": tid, "stage": stage,
             "status": "complete", "summary": str(path.relative_to(output)), "summary_sha256": sha256_file(path)}
        runner.save(path.parent / "result.json", r)
        return r

    monkeypatch.setattr(runner, "ensure_data", data)
    monkeypatch.setattr(runner, "execute_trial", execute)
    monkeypatch.setattr(sys, "argv", ["run.py", "--max-trials", "2"])
    assert runner.main() == 0
    output = next((tmp_path / "out").iterdir())
    assert runner.read(output / "report.json")["status"] == "trial_budget_reached"
    assert datasets == ["train"]
    monkeypatch.setattr(sys, "argv", ["run.py", "--resume", str(output)])
    assert runner.main() == 0
    assert runner.read(output / "report.json")["status"] == "complete"
    records = runner.get_records(output)
    assert len(records) == 34  # 5 coarse + 9 refine + 5 finalists x 2 windows x 2 seeds.
    plans = output / "plans/three_hot6/0.5"
    validate = runner.read(plans / "validate.json")
    assert all(records[p]["split"] == "train" for p in validate["parent_trials"])
    plan_hash = sha256_file(plans / "validate.json")
    previous_calls = len(calls)
    assert runner.main() == 0
    assert len(calls) == previous_calls and sha256_file(plans / "validate.json") == plan_hash
    # Mutating frozen config must not silently create new trial identities.
    with (output / "resolved.yaml").open("a") as f:
        f.write("\n# changed\n")
    with pytest.raises(ValueError, match="frozen config"):
        runner.main()


def test_missing_metrics_or_lost_transfers_rejected(tmp_path):
    p = tmp_path / "summary.json"
    runner.save(p, {"passed": False})
    with pytest.raises(ValueError, match="Incomplete"):
        runner.metrics_from_summary(p, "plain@.5", 1, 0)
