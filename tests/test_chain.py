"""_port_free semantics tests — 2026-09-03 incident: 连续两次实验之间，上一轮
anvil 留下的 TIME_WAIT 连接让未设 SO_REUSEADDR 的探针误报"端口占用"，
烧掉了一整次 50-pair 实验。TIME_WAIT 是合法可 bind 状态（真实服务器都设
SO_REUSEADDR），活跃监听者才是真占用。"""
import socket
import time
from unittest.mock import Mock

import pytest

from brokerlab import chain
from brokerlab.chain import AnvilCluster, _port_free
from brokerlab.config import ChainCfg


def test_port_free_not_fooled_by_time_wait():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    cli = socket.create_connection(("127.0.0.1", port), timeout=2)
    conn, _ = srv.accept()
    cli.close()
    conn.close()          # 服务端先关 established 连接 → 该端口留下 TIME_WAIT
    srv.close()
    time.sleep(0.2)       # 让 FIN/ACK 完成
    assert _port_free(port), "TIME_WAIT 不得被误报为占用（与 anvil 的 bind 语义一致）"


def test_port_free_detects_active_listener():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    try:
        assert not _port_free(port), "活跃监听者必须被正确判定为占用"
    finally:
        srv.close()


def test_connections_disable_environment_proxy_and_netrc(monkeypatch):
    captured = {}

    class FakeProvider:
        def __init__(self, endpoint, *, session, request_kwargs):
            captured.update(endpoint=endpoint, session=session,
                            request_kwargs=request_kwargs)

    class FakeWeb3:
        def __init__(self, provider):
            self.provider = provider

    monkeypatch.setattr(chain, "HTTPProvider", FakeProvider)
    monkeypatch.setattr(chain, "Web3", FakeWeb3)
    conns = chain.Connections(ChainCfg(num_shards=1, base_port=8600))

    first = conns.web3(0)
    assert conns.web3(0) is first
    assert captured["endpoint"] == "http://127.0.0.1:8600"
    assert captured["session"].trust_env is False
    assert captured["request_kwargs"] == {"timeout": 60}


def test_partial_cluster_start_failure_cleans_only_owned_children(monkeypatch, tmp_path):
    """第二个 Popen 失败时，第一个已启动子进程和两个日志句柄都必须回收。"""
    first = Mock()
    process_running = True
    first.poll.side_effect = lambda: None if process_running else 0
    def terminate():
        nonlocal process_running
        process_running = False
    first.terminate.side_effect = terminate
    first.pid = 111
    calls = 0

    def fake_popen(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise OSError("injected spawn failure")

    monkeypatch.setattr(chain, "resolve_anvil_bin", lambda *_: "anvil")
    monkeypatch.setattr(chain, "_port_free", lambda *_: True)
    monkeypatch.setattr(chain.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(chain.time, "sleep", lambda *_: None)
    cluster = AnvilCluster(ChainCfg(num_shards=2), tmp_path / "logs")

    with pytest.raises(OSError, match="injected"):
        cluster.start()

    first.terminate.assert_called_once_with()
    assert cluster._procs == {}
    assert cluster._log_files == {}
