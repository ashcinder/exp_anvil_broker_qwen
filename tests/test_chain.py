"""_port_free semantics tests — 2026-09-03 incident: 连续两次实验之间，上一轮
anvil 留下的 TIME_WAIT 连接让未设 SO_REUSEADDR 的探针误报"端口占用"，
烧掉了一整次 50-pair 实验。TIME_WAIT 是合法可 bind 状态（真实服务器都设
SO_REUSEADDR），活跃监听者才是真占用。"""
import socket
import time

from brokerlab.chain import _port_free


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
