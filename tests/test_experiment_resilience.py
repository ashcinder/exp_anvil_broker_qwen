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


def test_exp003_budget_allows_last_partial_release_block():
    module = _load("exp003_release_budget_test", "experiments/exp003_tdr_on_off/run.py")
    # 80000 CTX require 667 blocks, not 666.67 seconds, at 120 CTX/block.
    assert module._execution_budget_s(80000, 120, 5, 1800) == 5135
    assert module._execution_budget_s(80000, 120, 1, 1800) == 2467
    assert module._execution_budget_s(240, 120, 5, 0) == 10


def test_exp003_coordinator_survives_old_one_second_block_deadline(monkeypatch):
    import queue

    module = _load("exp003_coordinator_budget_test", "experiments/exp003_tdr_on_off/run.py")
    clock = SimpleNamespace(now=0.0)
    sleeps = []
    reads = 0

    def now():
        nonlocal reads
        reads += 1
        if reads > 2 and clock.now == 0:
            clock.now = 2468.0  # Old deadline would abort before full release.
        return clock.now

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now = 5136.0 if len(sleeps) == 1 else clock.now + 171.0

    monkeypatch.setattr(module.time, "monotonic", now)
    monkeypatch.setattr(module.time, "sleep", sleep)
    monkeypatch.setattr(module, "build_services", lambda _: (
        SimpleNamespace(block_number=lambda _: 0), SimpleNamespace(),
        SimpleNamespace(num_brokers=1)))
    params = dict(num_shards=1, num_brokers=1, balances_eth=45, route_seed=7,
                  release_poll_s=.15, max_backlog=80000, dispatch_per_tick=200,
                  loop_sleep_s=.005,
                  execution_budget_s=module._execution_budget_s(80000, 120, 5, 1800))
    ctx_queue, close_queue, done_queue = queue.Queue(), queue.Queue(), queue.Queue()
    close_queue.put(0)  # No in-flight engine work in this timeout-only fixture.
    module._routing_coordinator_worker(
        {}, params, 120, 2, [{}] * 80000, [ctx_queue], queue.Queue(),
        queue.Queue(), [queue.Queue()], close_queue, queue.Queue(), done_queue,
        SimpleNamespace(wait=lambda: None))
    result = done_queue.get_nowait()
    assert result["error"] is None
    assert result["aborted"].startswith("deadline")
    assert sleeps == [.005, .005]  # Continue past 2468s, then service the abort drain.
    assert ctx_queue.get_nowait() == module.END


def test_exp003_budget_accounts_for_sender_concentration_and_window():
    module = _load("exp003_sender_budget_test", "experiments/exp003_tdr_on_off/run.py")
    hot = [dict(sender_idx=200, src_shard=3)] * 6
    # Release all six in block 1; two-leg service needs three waves of two.
    assert module._execution_budget_s(6, 120, 5, 0, ctxs=hot, sender_window=2) == 35
    assert module._execution_budget_s(6, 120, 5, 0, ctxs=hot, sender_window=1) == 65
    spread = [dict(sender_idx=200+i, src_shard=3) for i in range(6)]
    assert module._execution_budget_s(6, 120, 5, 0, ctxs=spread, sender_window=2) == 15
    separate_shards = [dict(sender_idx=200, src_shard=i) for i in range(6)]
    assert module._execution_budget_s(6, 120, 5, 0, ctxs=separate_shards, sender_window=2) == 15


def test_exp003_abort_drain_preserves_real_pending_mint_receipt(monkeypatch):
    import queue

    module = _load("exp003_drain_test", "experiments/exp003_tdr_on_off/run.py")
    clock = SimpleNamespace(now=0.0, reads=0)
    rep, req, ack, close, done = (queue.Queue() for _ in range(5))
    req.put(dict(ref="T1.0", engine=0, receiver_idx=100, amount_wei=1, dst_shard=0))

    def now():
        clock.reads += 1
        if clock.reads > 2 and clock.now == 0:
            clock.now = 2.0
        return clock.now

    def sleep(_):
        clock.now += 1
        if not ack.empty():
            rep.put(("engine_closed", 0))
            close.put(0)

    tx = SimpleNamespace(
        block_number=lambda _: 0, send_transfer=lambda *_: "mint_hash",
        probe_receipt=lambda *_: (dict(status=1, block=1) if clock.now >= 3 else None))
    monkeypatch.setattr(module.time, "monotonic", now)
    monkeypatch.setattr(module.time, "sleep", sleep)
    monkeypatch.setattr(module, "build_services", lambda _: (
        tx, SimpleNamespace(address=lambda _: "broker"),
        SimpleNamespace(num_brokers=1, coordinator_index=1)))
    params = dict(num_shards=1, num_brokers=1, balances_eth=45, route_seed=7,
                  release_poll_s=.15, max_backlog=80000, dispatch_per_tick=200,
                  loop_sleep_s=.005, execution_budget_s=1, shutdown_grace_s=5,
                  probe_first_s=0, probe_s=.2, relay_timeout_s=120)
    module._routing_coordinator_worker(
        {}, params, 120, 2, [], [queue.Queue()], rep, req, [ack], close,
        queue.Queue(), done, SimpleNamespace(wait=lambda: None))
    result = done.get_nowait()
    assert result["error"] is None
    assert result["aborted"].startswith("deadline")
    assert result["closes"] == 1
    assert ack.get_nowait() == ("T1.0", 1, 1)  # Real success, not fabricated rejection.


def test_exp003_retains_individual_engine_results(tmp_path):
    module = _load("exp003_partial_result_test", "experiments/exp003_tdr_on_off/run.py")
    env = dict(broker_idx=4, rows=[dict(ctx_id="c1", ok=True)],
               tdr=dict(balance_snapshots=[dict(block=20, shard_0_balance=45)]))
    module._save_engine_result(tmp_path, env)
    assert json.loads((tmp_path / "engine_results/broker_04.json").read_text()) == env


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
