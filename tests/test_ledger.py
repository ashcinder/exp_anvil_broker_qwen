"""BrokerLedger 不变量单测（审计 F5 的防线形式化）。"""
import pytest

from brokerlab.ledger import BrokerLedger, LedgerError


def test_available_formula_and_flow():
    led = BrokerLedger({0: 100, 1: 50})
    assert led.available(0) == 100
    led.reserve(0, 60)
    assert led.available(0) == 40            # 预留立刻降低可用额
    led.confirm_credit(1, 25)                # 进账只有落定后才进 confirmed
    led.confirm_debit(0, 60)                 # 付出落定：reserved 与 confirmed 同减
    assert led.confirmed == {0: 40, 1: 75}
    assert led.reserved == {0: 0, 1: 0}


def test_reserve_over_available_rejected():
    led = BrokerLedger({0: 100})
    with pytest.raises(LedgerError):
        led.reserve(0, 101)


def test_release_pairing_and_no_negative():
    led = BrokerLedger({0: 100})
    led.reserve(0, 30)
    led.release(0, 30)
    assert led.available(0) == 100
    with pytest.raises(LedgerError):
        led.release(0, 1)                    # 释放不存在的预留


def test_f5_regression_reserved_funds_not_double_committed():
    """gen-3 的病：effective_balance 把未到账当可用 → TDR 搬走已预留的钱。
    这里预留 80 后 available=20：任何再扣 21 必须抛；incoming 根本不参与。"""
    led = BrokerLedger({0: 100})
    led.reserve(0, 80)
    with pytest.raises(LedgerError):
        led.reserve(0, 21)
    led.confirm_debit(0, 80)                 # 正常落定路径畅通
    assert led.available(0) == 20


def test_confirm_debit_requires_matching_reservation():
    led = BrokerLedger({0: 100})
    with pytest.raises(LedgerError):
        led.confirm_debit(0, 10)             # 没预留就不许"确认付出"
