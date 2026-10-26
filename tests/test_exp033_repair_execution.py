"""Current-payment rescue and settled-flow tests with real engine state transitions."""
import pytest
from test_tdr_hook import FakeTx, FakeUsers, FakeQ, _ctx
from brokerlab.tdr_engine import TdrAgent
from brokerlab.tdr_hook import TdrDynamicEngine
from brokerlab.broker_engine import END


def engine(policy, balances):
    tx = FakeTx()
    ctx, req, ack = FakeQ(), FakeQ(), FakeQ()
    agent = TdrAgent(2, policy=policy, chi_blocks=0, spider_vol_wei=100)
    e = TdrDynamicEngine(tx, FakeUsers(), broker_idx=0, broker_base_index=100,
                        ctx_q=ctx, rep_q=FakeQ(), init_wei_by_shard=balances,
                        credit_sink=req, credit_in=ack, tdr_agent=agent,
                        probe_s=0, probe_first_s=0, block_poll_s=0,
                        clock=lambda: tx.now)
    return e, tx, ctx, req, ack, agent


@pytest.mark.parametrize('policy', ['ucrb', 'flexichain'])
def test_rescue_holds_and_retries_same_payment_after_mint(policy):
    e, tx, ctx, req, ack, agent = engine(policy, {0: 2, 1: 100})
    ctx.put(_ctx(0, 200, 1, 0, 240, 10))
    ctx.put(END)
    tx.now = 1
    e.tick_once()
    assert e._blocked_ctx['ctx_id'] == 'c0000'
    assert not tx.submitted  # payment has not been relayed or sent
    for _ in range(4):
        tx.now += 1
        e.tick_once()
    request = req.get_nowait()
    assert request['ref'].startswith('T') and request['amount_wei'] == 8
    assert not e._open and not e._ended
    ack.put((request['ref'], 1, tx.h + 1))
    for _ in range(8):
        tx.now += 1
        e.tick_once()
    assert e.done() and len(e._rows) == 1
    row = e._rows[0]
    assert row['ctx_id'] == 'c0000' and row['route'] == 'broker' and row['ok']
    assert row['repair_wait_secs'] > 0 and row['e2e_secs'] >= row['repair_wait_secs']
    assert agent.transfers_done == 1 and e.n_rcvd == 1


@pytest.mark.parametrize('policy', ['ucrb', 'flexichain'])
def test_infeasible_rescue_falls_back_without_partial_burn(policy):
    e, tx, ctx, req, ack, agent = engine(policy, {0: 2, 1: 3})
    ctx.put(_ctx(0, 200, 1, 0, 240, 10))
    for _ in range(3):
        tx.now += 1
        e.tick_once()
    assert agent.events_opened == 0 and agent.moved_wei == 0
    assert e._blocked_ctx is None and e._open['c0000'].route == 'relay'
    assert req.get_nowait()['ref'] == 'c0000'


def test_spider_counts_only_successful_broker_settlement():
    e, tx, ctx, req, ack, agent = engine('spider_vol', {0: 100, 1: 100})
    base = dict(ctx_id='test', e2e_secs=1., amount_wei=50, submit_block=100, src=1, dst=0)
    e._emit(dict(base, route='relay', ok=True))
    e._emit(dict(base, route='broker', ok=False))
    assert agent._flowed_wei == 0
    e._emit(dict(base, route='broker', ok=True))
    assert agent._flowed_wei == 50
