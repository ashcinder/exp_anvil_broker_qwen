"""exp007/008 初始化所有权与不完整报告的回归测试。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brokerlab.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[
    ("exp007_run_test", "experiments/exp007_dynamic_routing/run.py"),
    ("exp003_run_test", "experiments/exp003_tdr_on_off/run.py"),
])
def run_module(request):
    return _load(*request.param)


def _minimal_cfg():
    return SimpleNamespace(
        chain=SimpleNamespace(mnemonic="test", num_shards=1),
        scale=SimpleNamespace(num_brokers=0, coordinator_index=1,
                              broker_base_index=100),
        exp={"balances_eth": 1,
             "coordinator_mint_budget_eth_per_shard": 1000},
        b2e=SimpleNamespace(enabled=False, fee_share=0.0,
                            gas_units_per_ctx=21000,
                            ref_gas_price_wei=0,
                            charges_rebalancing=False),
    )


class _FakeCluster:
    instances = []

    def __init__(self, *_):
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def write_pids(self, path):
        Path(path).write_text("{}")


class _FakeUsers:
    def __init__(self, *_):
        pass

    def address(self, index):
        return f"address-{index}"


class _FakeTx:
    def __init__(self, *_):
        pass

    def sync_nonces(self, *_):
        pass

    def set_balance_setup(self, *_):
        pass

    def get_balance(self, *_):
        return 123


class _Watchdog:
    def track(self, *_):
        pass


def _patch_funding(monkeypatch, module, connection_type):
    _FakeCluster.instances.clear()
    monkeypatch.setattr(module, "AnvilCluster", _FakeCluster)
    monkeypatch.setattr(module, "Connections", connection_type)
    monkeypatch.setattr(module, "UserManager", _FakeUsers)
    monkeypatch.setattr(module, "TxService", _FakeTx)


def test_fund_chain_cleans_owned_cluster_when_initialization_fails(
        monkeypatch, tmp_path, run_module):
    class FailingConnections:
        def __init__(self, *_):
            pass

        def wait_all_ready(self):
            raise KeyboardInterrupt("injected Ctrl+C")

    _patch_funding(monkeypatch, run_module, FailingConnections)
    with pytest.raises(KeyboardInterrupt, match="injected"):
        run_module.fund_chain(_minimal_cfg(), [], 0, tmp_path, _Watchdog())

    cluster = _FakeCluster.instances[-1]
    assert cluster.started
    assert cluster.stopped


def test_fund_chain_success_keeps_cluster_alive(
        monkeypatch, tmp_path, run_module):
    class WorkingConnections:
        def __init__(self, *_):
            pass

        def wait_all_ready(self):
            pass

    _patch_funding(monkeypatch, run_module, WorkingConnections)
    result = run_module.fund_chain(_minimal_cfg(), [], 0, tmp_path, _Watchdog())

    cluster = result[0]
    assert cluster.started
    assert not cluster.stopped
    cluster.stop()


def test_exp007_empty_or_missing_results_are_not_complete():
    module = _load("exp007_status_test", "experiments/exp007_dynamic_routing/run.py")
    empty = module.evaluate_run_status([], {}, ["stat_120", "dyn_120"], False)
    assert not empty["complete"] and not empty["gates_ok"]
    assert empty["missing_arms"] == ["dyn_120", "stat_120"]

    partial = module.evaluate_run_status(
        [{"tag": "stat_120"}], {"stat_120:G1_all_ok": True},
        ["stat_120", "dyn_120"], False)
    assert not partial["complete"]
    assert partial["missing_arms"] == ["dyn_120"]


def test_exp003_empty_results_are_not_complete():
    module = _load("exp003_status_test", "experiments/exp003_tdr_on_off/run.py")
    status = module.evaluate_run_status({}, {}, ["plain@150.0", "tdr@150.0@0.1"])
    assert not status["complete"]
    assert not status["gates_ok"]
    assert status["missing_arms"] == ["plain@150.0", "tdr@150.0@0.1"]


def test_exp008_missing_sessions_and_failed_subprocess_cannot_pass(tmp_path):
    module = _load("exp008_status_test", "experiments/exp008_tdr_schedule_final/run.py")
    cfg = load_config(ROOT / "experiments/exp008_tdr_schedule_final/config.yaml")
    missing_dir = tmp_path / "session_1" / "run"
    missing_dir.mkdir(parents=True)
    report = module.aggregate(
        tmp_path, [missing_dir], cfg,
        session_runs=[{"index": 1, "path": str(missing_dir), "returncode": 1}],
    )

    assert not report["status"]["complete"]
    assert report["status"]["failed_sessions"] == [1]
    assert report["status"]["missing_summary_sessions"] == list(
        range(1, int(cfg.exp["exp008_sessions"]) + 1))
    assert report["verdicts"]["V1_gates_every_session"] is False
    assert report["verdicts"]["V3_final_throughput_loss"]["pass"] is False
    assert report["verdict_all"] is False


def test_exp008_nonzero_child_exit_rejects_even_complete_summary(tmp_path):
    module = _load("exp008_rc_test", "experiments/exp008_tdr_schedule_final/run.py")
    cfg = load_config(ROOT / "experiments/exp008_tdr_schedule_final/config.yaml")
    cfg.exp["exp008_sessions"] = 1
    result_dir = tmp_path / "session_1" / "run"
    result_dir.mkdir(parents=True)
    arms = []
    for tag in module._expected_arm_tags(cfg):
        arms.append({
            "arm": tag, "engine": "plain" if tag.startswith("plain") else "topup",
            "fund_eth": 150.0, "relayed": 0,
            "tdr": {"events_opened": 0, "transfers_done": 0, "moved_wei": 0},
            "legs": {"tdr_share_of_ctx_legs": 0.0},
            "throughput_ctx_per_s": 1.0,
            "gates": {"G1": True}, "burn_reconcile": {"ok": True},
        })
    (result_dir / "summary.json").write_text(json.dumps({
        "params": {"_runtime": {"total_ctx": 4}},
        "arms": arms, "passed": True,
    }))

    report = module.aggregate(
        tmp_path, [result_dir], cfg,
        session_runs=[{"index": 1, "path": str(result_dir), "returncode": 1}],
    )
    assert report["status"]["failed_sessions"] == [1]
    assert report["status"]["missing_summary_sessions"] == []
    assert report["status"]["missing_arms_by_session"] == {}
    assert report["status"]["unpassed_summary_sessions"] == []
    assert report["status"]["complete"] is False
    assert report["verdict_all"] is False


def test_exp008_uses_paired_session_reductions(tmp_path):
    module = _load("exp008_paired_test", "experiments/exp008_tdr_schedule_final/run.py")
    cfg = load_config(ROOT / "experiments/exp008_tdr_schedule_final/config.yaml")
    cfg.exp["exp008_sessions"] = 2
    tags = module._expected_arm_tags(cfg)
    plain = next(t for t in tags if t.startswith("plain@"))
    final = cfg.exp["exp008_final_arm"]
    session_dirs = []
    for index, (plain_relay, final_relay) in enumerate(((100, 70), (200, 150)), 1):
        result_dir = tmp_path / f"session_{index}" / "run"
        result_dir.mkdir(parents=True)
        arms = []
        for tag in tags:
            relay = (plain_relay if tag == plain else
                     final_relay if tag == final else 80)
            arms.append({
                "arm": tag, "engine": "plain" if tag == plain else "topup",
                "fund_eth": 8.0, "relayed": relay,
                "tdr": {"events_opened": 0, "transfers_done": 0, "moved_wei": 0},
                "legs": {"tdr_share_of_ctx_legs": 0.0},
                "throughput_ctx_per_s": 100.0,
                "gates": {"G1": True}, "burn_reconcile": {"ok": True},
            })
        (result_dir / "summary.json").write_text(json.dumps({
            "params": {"_runtime": {"total_ctx": 1000}},
            "arms": arms, "passed": True,
        }))
        session_dirs.append(result_dir)
    report = module.aggregate(
        tmp_path, session_dirs, cfg,
        session_runs=[{"index": i, "path": str(path), "returncode": 0}
                      for i, path in enumerate(session_dirs, 1)],
    )
    paired = report["verdicts"]["V2_final_relay_reduction"]["comparison"][final][
        "paired_reduction"]
    assert paired["n"] == 2
    assert paired["median"] == pytest.approx(0.275)
    assert report["verdicts"]["V2_final_relay_reduction"]["pass"] is True
