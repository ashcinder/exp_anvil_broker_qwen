"""No chains/processes: validate config resolution and downstream overrides."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/exp008_tdr_schedule_final/run.py"
spec = importlib.util.spec_from_file_location("exp008_valve_grid", PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def config():
    # Grid tests must not depend on which experiment mode is currently selected.
    cfg = runner.load_config(PATH.parent / "config.yaml")
    cfg.exp.update(exp008_valve_sweep=True,
                   exp008_valve_thresholds=[1.05, 1.1, 1.3, 1.5, 2.0, 2.5],
                   exp008_ctx_per_method=10000, exp008_sessions=1, balances_eth=0.5)
    return cfg


def test_default_grid_matches_child_config():
    base = config()
    resolved, overrides = runner.prepare_valve_sweep(base)
    child = runner.apply_overrides(base, overrides)
    assert child == resolved
    tags = runner._expected_arm_tags(resolved)
    assert len(tags) == len(set(tags)) == 10
    assert [tag for tag in tags if tag.startswith("valve")] == [
        f"valve@0.5@{v}" for v in [1.05, 1.1, 1.3, 1.5, 2.0, 2.5]]
    assert resolved.exp["ctx_per_broker"] * resolved.scale.num_brokers == 10000
    assert resolved.exp["exp008_sessions"] == 1
    assert resolved.broker.initial_balance_eth == 0.5
    assert resolved.exp["exp008_final_arm"] in tags


def test_edit_balance_count_sessions():
    cfg = runner.apply_overrides(config(), ["exp.balances_eth=0.3",
          "exp.exp008_ctx_per_method=20000", "exp.exp008_sessions=2"])
    resolved, _ = runner.prepare_valve_sweep(cfg)
    assert resolved.broker.initial_balance_eth == 0.3
    assert all(t.split("@")[1] == "0.3" for t in runner._expected_arm_tags(resolved))
    assert resolved.exp["ctx_per_broker"] == 400
    assert resolved.exp["exp008_sessions"] == 2


@pytest.mark.parametrize("key,value", [
    ("exp008_valve_thresholds", []), ("exp008_valve_thresholds", [1.1, 1.1]),
    ("exp008_valve_thresholds", [1]), ("exp008_valve_thresholds", [float("nan")]),
    ("exp008_ctx_per_method", 10001), ("exp008_ctx_per_method", 0),
    ("exp008_sessions", 0), ("exp008_sessions", 1.5),
    ("balances_eth", -0.5), ("exp008_valve_sweep", "false"),
])
def test_invalid_settings(key, value):
    cfg = config()
    cfg.exp[key] = value
    with pytest.raises(ValueError):
        runner.prepare_valve_sweep(cfg)


def test_legacy_arms_untouched():
    cfg = runner.apply_overrides(config(), ["exp.exp008_valve_sweep=false"])
    assert runner.prepare_valve_sweep(cfg) == (cfg, [])


def test_dry_run_no_subprocess(monkeypatch, capsys):
    config_for_test = config()
    monkeypatch.setattr(runner, "load_config", lambda path: config_for_test)
    monkeypatch.setattr(runner.sys, "argv", [str(PATH), "--dry-run"])
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not start experiments")
    monkeypatch.setattr(runner.subprocess, "call", forbidden)
    assert runner.main() == 0
    assert 'valve@0.5@1.05' in capsys.readouterr().out
