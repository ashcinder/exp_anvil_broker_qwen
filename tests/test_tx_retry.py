"""TxService 只对 Anvil latest-block 瞬时竞态做有限、只读重试。"""
from unittest.mock import Mock

import pytest
from web3.exceptions import Web3RPCError

from brokerlab.tx import TxService


def _service(get_balance):
    service = TxService.__new__(TxService)
    web3 = Mock()
    web3.eth.get_balance = get_balance
    conns = Mock()
    conns.web3.return_value = web3
    service._conns = conns
    return service, conns


def _block_error(marker):
    return Web3RPCError(
        f"BlockOutOfRangeError: block height is {marker + 1} "
        f"but requested was {marker}"
    )


def test_get_balance_retries_once_then_returns_real_value(monkeypatch):
    rpc = Mock(side_effect=[_block_error(12), 987654321])
    service, conns = _service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    assert service.get_balance("0xabc", 7) == 987654321
    assert rpc.call_count == 2
    rpc.assert_called_with("0xabc")
    conns.web3.assert_called_with(7)
    sleep.assert_called_once_with(0.05)


def test_get_balance_raises_original_error_after_five_attempts(monkeypatch):
    errors = [_block_error(i) for i in range(5)]
    rpc = Mock(side_effect=errors)
    service, _ = _service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    with pytest.raises(Web3RPCError) as caught:
        service.get_balance("0xdef", 3)

    assert caught.value is errors[-1]
    assert rpc.call_count == 5
    assert sleep.call_count == 4


def test_get_balance_does_not_retry_other_rpc_errors(monkeypatch):
    error = Web3RPCError("execution reverted")
    rpc = Mock(side_effect=error)
    service, _ = _service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    with pytest.raises(Web3RPCError) as caught:
        service.get_balance("0x123", 1)

    assert caught.value is error
    assert rpc.call_count == 1
    sleep.assert_not_called()


def _nonce_service(get_transaction_count):
    service = TxService.__new__(TxService)
    web3 = Mock()
    web3.eth.get_transaction_count = get_transaction_count
    conns = Mock()
    conns.web3.return_value = web3
    service._conns = conns
    service._users = Mock()
    service._users.address.return_value = "0xnonce"
    service.nonces = Mock()
    return service, conns


def test_sync_nonces_retries_once_then_syncs_real_nonce(monkeypatch):
    rpc = Mock(side_effect=[_block_error(6), 17])
    service, conns = _nonce_service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    service.sync_nonces([376], [9])

    assert rpc.call_count == 2
    rpc.assert_called_with("0xnonce", "pending")
    conns.web3.assert_called_with(9)
    sleep.assert_called_once_with(0.05)
    service.nonces.sync.assert_called_once_with("0xnonce", 9, 17)


def test_sync_nonces_raises_original_error_after_five_attempts(monkeypatch):
    errors = [_block_error(i) for i in range(5)]
    rpc = Mock(side_effect=errors)
    service, _ = _nonce_service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    with pytest.raises(Web3RPCError) as caught:
        service.sync_nonces([376], [9])

    assert caught.value is errors[-1]
    assert rpc.call_count == 5
    assert sleep.call_count == 4
    service.nonces.sync.assert_not_called()


def test_sync_nonces_does_not_retry_other_rpc_errors(monkeypatch):
    error = Web3RPCError("connection refused")
    rpc = Mock(side_effect=error)
    service, _ = _nonce_service(rpc)
    sleep = Mock()
    monkeypatch.setattr("brokerlab.tx.time.sleep", sleep)

    with pytest.raises(Web3RPCError) as caught:
        service.sync_nonces([376], [9])

    assert caught.value is error
    assert rpc.call_count == 1
    sleep.assert_not_called()
    service.nonces.sync.assert_not_called()
