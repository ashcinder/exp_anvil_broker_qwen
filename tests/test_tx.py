"""TxService 出块竞态重试的回归测试（co-author bug：BlockOutOfRangeError）。

错误形态（anvil --block-time=1 + --prune-history）：
  Web3RPCError: {'code': -32602, 'message': 'BlockOutOfRangeError:
  block height is 387 but requested was 386'}
latest 读在"解析→读状态"窗口撞上新区块落地 ⇒ 瞬时失败、可重查恢复。
"""
import pytest

from brokerlab.tx import TxService


class _FakeEth:
    def __init__(self, script):
        self.script = script          # 前 n 次抛错的异常列表，之后返回 ok 值
        self.calls = 0

    def get_balance(self, addr):
        self.calls += 1
        if self.calls <= len(self.script):
            raise self.script[self.calls - 1]
        return 42


class _FakeWeb3:
    def __init__(self, eth):
        self.eth = eth


class _FakeConns:
    def __init__(self, eth):
        self._w3 = _FakeWeb3(eth)

    def web3(self, shard):
        return self._w3


def _svc(script):
    eth = _FakeEth(script)
    return TxService(_FakeConns(eth), users=None, chain_cfg=None), eth


TRANSIENT = RuntimeError(
    "{'code': -32602, 'message': 'BlockOutOfRangeError: "
    "block height is 387 but requested was 386'}")


def test_transient_block_error_is_retried(monkeypatch):
    """瞬时错误重试后成功；重试次数 = 失败次数 + 1。"""
    monkeypatch.setattr("brokerlab.tx.time.sleep", lambda s: None)
    svc, eth = _svc([TRANSIENT, TRANSIENT])
    assert svc.get_balance("0xabc", 0) == 42
    assert eth.calls == 3


def test_other_rpc_error_raises_immediately():
    """非竞态错误（如连接被拒）不得被吞：一次即抛。"""
    svc, eth = _svc([ConnectionError("boom")])
    with pytest.raises(ConnectionError):
        svc.get_balance("0xabc", 0)
    assert eth.calls == 1


def test_persistent_transient_error_gives_up(monkeypatch):
    """持续竞态：达到 tries 上限后照常抛出，不无限重试。"""
    monkeypatch.setattr("brokerlab.tx.time.sleep", lambda s: None)
    svc, eth = _svc([TRANSIENT] * 10)
    with pytest.raises(RuntimeError):
        svc.get_balance("0xabc", 0)
    assert eth.calls == 8              # 默认 tries=8
