"""Calibration orchestration tests: no Anvil processes or real experiments."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/exp008_tdr_schedule_final/calibrate_balance.py"
spec = importlib.util.spec_from_file_location("exp008_calibration", SCRIPT)
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)


def fake_summary(balance, *, ewma_relay=120):
    arms = []
    for method, tag in zip(cal.METHODS, cal.expected_tags(balance)):
        arms.append(dict(arm=tag, n=5000, failed=0, fund_eth=balance,
                         relayed=ewma_relay if method == "ewma" else 150,
                         gates={"ok": True}, burn_reconcile={"ok": True},
                         tdr=dict(events_opened=10 if method != "plain" else 0,
                                  transfers_done=20 if method != "plain" else 0,
                                  transfers_lost=0, moved_wei=10**18),
                         legs=dict(tdr_legs=40 if method != "plain" else 0),
                         throughput_ctx_per_s=115,
                         coord=dict(achieved_injection_ctx_per_s=120)))
    return dict(arms=arms, passed=True, status=dict(complete=True))


def test_fractional_balance_all_three_entries_and_tags():
    base = cal.to_params_dict(cal.load_config(SCRIPT.parent / "config.yaml"))
    for balance in cal.BALANCES:
        for pilot, count in ((True, 5000), (False, 40000)):
            data = cal.configured(base, balance, pilot=pilot)
            assert data["broker"]["initial_balance_eth"] == balance
            assert data["exp"]["balances_eth"] == balance
            tokens = data["exp"]["arms"].split(",")
            assert len(tokens) == 5
            assert all(float(t.split("@")[1]) == balance for t in tokens)
            assert float(tokens[1].split("@")[2]) == 10.0
            assert data["exp"]["tdr_cap_mult"] == 10.0
            assert "stress control" in data["exp"]["exp008_design_note"]
            assert data["exp"]["exp008_final_arm"] == cal.expected_tags(balance)[-1]
            assert data["exp"]["ctx_per_broker"] * data["scale"]["num_brokers"] == count
            assert data["exp"]["route_seed"] == (107 if pilot else 7)


def test_reader_ranking_and_no_fake_win(tmp_path):
    rows = []
    for balance, relay in ((0.1, 160), (0.2, 155)):
        path = tmp_path / f"{balance}.json"
        path.write_text(json.dumps(fake_summary(balance, ewma_relay=relay)))
        rows.extend(cal.read_trial(path, balance))
    ranked = cal.rank_candidates(rows)
    assert ranked[0]["balance_eth"] == 0.2
    assert ranked[0]["relay_margin"] < 0
    assert not ranked[0]["ewma_beats_all_relay"]
    assert ranked[0]["ewma_relay_plus_transfers"] == 175
    # Zero-pressure candidates must not win by saving idle liquidity.
    for row in rows:
        if row["balance_eth"] == 0.2 and row["method"] == "plain":
            row["relay_rate"] = 0
    assert cal.rank_candidates(rows)[0]["balance_eth"] == 0.1


@pytest.mark.parametrize("corruption", ["count", "missing", "gate", "lost", "duplicate", "rounded_balance"])
def test_invalid_trial_rejected(tmp_path, corruption):
    data = fake_summary(0.1)
    if corruption == "count":
        data["arms"][0]["n"] = 4999
    elif corruption == "missing":
        data["arms"].pop()
    elif corruption == "gate":
        data["arms"][0]["gates"]["ok"] = False
    elif corruption == "lost":
        data["arms"][1]["tdr"]["transfers_lost"] = 1
    elif corruption == "rounded_balance":
        data["arms"][0]["fund_eth"] = 0.01
    else:
        data["arms"].append(data["arms"][0])
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        cal.read_trial(path, 0.1)


def test_dry_run_never_starts_experiment(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not call processes or inspect ports")
    monkeypatch.setattr(cal, "call_logged", forbidden)
    monkeypatch.setattr(cal, "check_ports", forbidden)
    assert cal.main(["--dry-run"]) == 0


def test_requested_grid_and_small_balance_metadata(tmp_path):
    assert cal.BALANCES == (0.005, 0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5)
    path = tmp_path / "summary.json"
    data = fake_summary(0.005)
    path.write_text(json.dumps(data))
    rows = cal.read_trial(path, 0.005)
    assert all(r["balance_eth"] == 0.005 for r in rows)
    assert rows[1]["valve_cap_mult"] == 10.0
    data["arms"][0]["fund_eth"] = round(0.005, 2)
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        cal.read_trial(path, 0.005)


def test_valve_stress_threshold_delays_trigger():
    from brokerlab.tdr_policy import valve_plans
    init = 5 * 10**15  # 0.005 ETH
    actual = [2 * init, 0] + [init] * 14
    assert valve_plans(actual, init, 1.3)
    assert valve_plans(actual, init, cal.VALVE_CAP_MULT) == []
    # The 10x threshold is not an unconditional disable: reaching it can trigger.
    actual = [10 * init] + [0] * 9 + [init] * 6
    assert valve_plans(actual, init, cal.VALVE_CAP_MULT)


@pytest.mark.parametrize("edit_during_run", [False, True])
def test_pilot_only_orchestration_with_mocked_experiments(tmp_path, monkeypatch, edit_during_run):
    config = tmp_path / "config.yaml"
    original = (SCRIPT.parent / "config.yaml").read_bytes()
    config.write_bytes(original)
    monkeypatch.setattr(cal, "HERE", tmp_path)
    monkeypatch.setattr(cal, "check_ports", lambda base: None)
    monkeypatch.setattr(cal, "sha256", lambda path: "test-only")
    calls = []

    def fake_call(command, logfile):
        calls.append(command)
        cfg = yaml.safe_load(Path(command[command.index("--config") + 1]).read_text(encoding="utf-8"))
        out = Path(command[command.index("--out-root") + 1]) / "fake_time"
        out.mkdir(parents=True)
        (out / "summary.json").write_text(json.dumps(fake_summary(cfg["exp"]["balances_eth"])))
        if edit_during_run:
            config.write_bytes(original + b"\n# concurrent user edit\n")
        return 0

    monkeypatch.setattr(cal, "call_logged", fake_call)
    if edit_during_run:
        with pytest.raises(RuntimeError, match="Config changed"):
            cal.main(["--config", str(config), "--pilot-only"])
        assert config.read_bytes().endswith(b"# concurrent user edit\n")
    else:
        assert cal.main(["--config", str(config), "--pilot-only"]) == 0
        selected = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert selected["exp"]["balances_eth"] == 0.005
        assert selected["exp"]["exp008_sessions"] == 2
        assert selected["exp"]["ctx_per_broker"] == 800
    assert len(calls) == 8
    jobs = list((tmp_path / "calibration").iterdir())
    assert len(jobs) == 1
    assert (jobs[0] / "config.before.yaml").read_bytes() == original
    assert (jobs[0] / "pilot_metrics.csv").exists()


@pytest.mark.parametrize("informative", [False, True])
def test_full_pipeline_negative_result_and_no_pressure(tmp_path, monkeypatch, informative):
    config = tmp_path / "config.yaml"
    original = (SCRIPT.parent / "config.yaml").read_bytes()
    config.write_bytes(original)
    monkeypatch.setattr(cal, "HERE", tmp_path)
    monkeypatch.setattr(cal, "check_ports", lambda base: None)
    monkeypatch.setattr(cal, "sha256", lambda path: "test-only")
    calls = []

    def fake_call(command, logfile):
        calls.append(command)
        cfg = yaml.safe_load(Path(command[command.index("--config") + 1]).read_text(encoding="utf-8"))
        if "--out-root" in command:
            out = Path(command[command.index("--out-root") + 1]) / "fake_time"
            out.mkdir(parents=True)
            summary = fake_summary(cfg["exp"]["balances_eth"], ewma_relay=160)
            if not informative:
                summary["arms"][0]["relayed"] = 0
            (out / "summary.json").write_text(json.dumps(summary))
            return 0
        out = tmp_path / "out" / "formal_time"
        out.mkdir(parents=True)
        # A complete experiment may fail the improvement hypothesis: preserve it.
        (out / "report.json").write_text(json.dumps(dict(
            params=cfg, status=dict(complete=True), verdict_all=False)))
        return 1

    monkeypatch.setattr(cal, "call_logged", fake_call)
    assert cal.main(["--config", str(config)]) == (1 if informative else 2)
    assert len(calls) == (9 if informative else 8)
    status_path = next((tmp_path / "calibration").glob("*/status.json"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == ("complete_hypothesis_not_met" if informative else "no_informative_candidate")
    if not informative:
        assert config.read_bytes() == original
