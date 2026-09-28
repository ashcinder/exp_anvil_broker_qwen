"""B2E 费用拆分与机制层段序列单测（纯函数 + FakeTx，无 anvil）。"""
import pytest

from brokerlab import config as C
from brokerlab.brokerchain import BURN_ADDRESS, BrokerChain, CTX, ROUTE_BROKER, ROUTE_RELAY

GWEI = 10 ** 9


def _cfg(**b2e):
    return C.RunCfg(b2e=C.B2ECfg(**b2e))


# ---------------------------------------------------------------------------
# 费用算术
# ---------------------------------------------------------------------------

def test_fees_zero_when_disabled():
    assert C.b2e_fees(_cfg(enabled=False, fee_share=0.5)) == (0, 0, 0)


def test_fee_split_endpoints_and_exact_sum():
    for beta in (0.0, 0.10, 0.333, 0.999, 1.0):
        f, fb, fx = C.b2e_fees(_cfg(enabled=True, fee_share=beta))
        assert fb + fx == f
        assert fb == f * int(round(beta * C.BETA_SCALE)) // C.BETA_SCALE


def test_fee_floor_not_float():
    """0.07×2.1e13 在浮点下是 1.46999…e12——floor 会差 1 wei。
    定点整数算法必须给出精确的 1.47e12。"""
    f, fb, fx = C.b2e_fees(_cfg(enabled=True, fee_share=0.07,
                                gas_units_per_ctx=21000,
                                ref_gas_price_wei=GWEI))
    assert f == 21000 * GWEI
    assert fb == 1_470_000_000_000
    assert fx == f - fb


def test_b2e_validation_and_override():
    with pytest.raises(C.ConfigError):
        C.validate(_cfg(enabled=True, fee_share=1.5))
    with pytest.raises(C.ConfigError):
        C.validate(_cfg(gas_units_per_ctx=0))
    with pytest.raises(C.ConfigError):
        C.validate(_cfg(ref_gas_price_wei=-1))
    cfg = C.apply_overrides(_cfg(), ["b2e.enabled=true", "b2e.fee_share=0.25",
                                     "b2e.ref_gas_price_wei=100000000000"])
    assert cfg.b2e.enabled is True
    assert cfg.b2e.fee_share == 0.25 and isinstance(cfg.b2e.fee_share, float)
    f, fb, _ = C.b2e_fees(cfg)
    assert f == 21000 * 100 * GWEI and fb == f // 4


# ---------------------------------------------------------------------------
# 机制层段序列（FakeTx 脚本化）
# ---------------------------------------------------------------------------

class FakeUsers:
    def address(self, idx):
        return f"0x{idx:040x}"


class FakeTx:
    def __init__(self, fail_call=None):
        self.calls = []
        self.fail_call = fail_call

    def send_transfer(self, from_idx, to_address, amount_wei, shard):
        self.calls.append((from_idx, to_address, int(amount_wei), shard))
        return f"0x{len(self.calls):064x}"

    def submit_block(self, h):
        return 10 + int(h, 16)

    def tx_bytes(self, h):
        return 108

    def wait_receipt(self, h, shard, timeout_s=90.0):
        n = int(h, 16)
        status = 0 if self.fail_call == n else 1
        return {"status": status, "block": 100 + n, "receipt_bytes": 800}


def _ctx(v=7 * GWEI):
    return CTX("t0", sender_idx=200, receiver_idx=240, src_shard=0, dst_shard=1,
               amount_wei=v)


def _bc(tx):
    return BrokerChain(tx, FakeUsers(), coordinator_index=1, broker_base_index=100)


BROKER0 = f"0x{100:040x}"
RECV = f"0x{240:040x}"


def test_no_fees_byte_identical_leg_sequence():
    tx = FakeTx()
    res = _bc(tx).execute(_ctx(), ROUTE_BROKER, 0)
    v = _ctx().amount_wei
    assert tx.calls == [(200, BROKER0, v, 0), (100, RECV, v, 1)]
    assert res.ok and res.theta1b is None and res.fee_broker_wei == 0


def test_broker_route_three_legs_with_fees():
    tx = FakeTx()
    f, fb, fx = C.b2e_fees(_cfg(enabled=True, fee_share=0.10,
                                ref_gas_price_wei=100 * GWEI))
    v = _ctx().amount_wei
    res = _bc(tx).execute(_ctx(), ROUTE_BROKER, 0,
                          fee_broker_wei=fb, fee_burn_wei=fx)
    assert tx.calls == [(200, BROKER0, v + fb, 0),
                        (200, BURN_ADDRESS, fx, 0),
                        (100, RECV, v, 1)]
    assert res.ok and res.theta1b is not None and res.theta1b.ok
    assert res.fee_broker_wei == fb and res.fee_burn_wei == fx
    assert fb + fx == f


def test_relay_route_burns_full_fee_in_one_leg():
    tx = FakeTx()
    _, fb, fx = C.b2e_fees(_cfg(enabled=True, fee_share=0.10,
                                ref_gas_price_wei=100 * GWEI))
    v = _ctx().amount_wei
    res = _bc(tx).execute(_ctx(), ROUTE_RELAY,
                          fee_broker_wei=0, fee_burn_wei=fx + fb)  # relay 政策：全额烧
    assert tx.calls == [(200, BURN_ADDRESS, v + fx + fb, 0), (1, RECV, v, 1)]
    assert res.ok and res.theta1b is None


def test_burn_leg_failure_gates_theta2():
    tx = FakeTx(fail_call=2)          # 第 2 笔（Θ1b）revert
    res = _bc(tx).execute(_ctx(), ROUTE_BROKER, 0,
                          fee_broker_wei=1000, fee_burn_wei=2000)
    assert res.theta1.ok and res.theta1b is not None and not res.theta1b.ok
    assert res.theta2 is None        # 承诺：Θ1 段未全部落定 ⇒ Θ2 不发
    assert not res.ok
    assert len(tx.calls) == 2        # 只发了 Θ1a 与 Θ1b
