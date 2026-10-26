"""Literature counterexamples and bounded planning invariants, revised v2."""
import itertools
import pytest
from brokerlab.sota_policies import (autoloop_plans, autoloop_requests,
    flexichain_plans, loopmax_requests, loopmax_plans, overpass_target,
    pnps_plan, pnps_target, spider_plans, ucrb_plans, uniform_target)
from brokerlab.tdr_engine import TdrAgent
ETH = 10**18

def _agent(policy, **kw):
    return TdrAgent(4, window_blocks=10, chi_blocks=0, offset_blocks=0,
                    policy=policy, demand_ewma_half_life=10., **kw)

def test_rescue_is_exact_and_not_periodic_equalization():
    assert flexichain_plans([0, 900]) == []
    assert flexichain_plans([0, 900], {0: 5}) == [(1, 0, 5)]
    assert ucrb_plans([0, 100, 50], {0: 300}) == []
    assert ucrb_plans([0, 500, 50], {0: 300}) == [(1, 0, 300)]
    assert _agent('ucrb').tick(10, [0, 900, 0, 0]) == []

def test_autoloop_leaves_in_band_accounts_untouched():
    assert autoloop_requests([40, 100], 100) == [0, -50]
    assert autoloop_plans([40, 100], 100) == []
    assert autoloop_plans([10, 90, 40], 100) == [(1, 0, 40)]
    assert autoloop_requests([30, 70], 100) == [0, 0]

def test_loopmax_has_depletion_and_saturation_and_maximum_amount():
    assert loopmax_requests([10, 90], [-2, 2], [100, 100], 20, 2) == [86, -86]
    assert loopmax_requests([999, 1000], [10, -10], [1000, 1000], 30, 0) == [-999, 0]
    assert loopmax_requests([50, 50], [-.1, .1], [100, 100], 20, 0) == [0, 0]
    assert loopmax_plans([10, 90], [20, 0], [0, 20], 0, 10, 20, 2,
                         capacities=[100, 100]) == [(1, 0, 86)]
    with pytest.raises(ValueError, match='explicit'):
        loopmax_plans([10, 90], [20, 0], [0, 20], 0, 10, 20, 2)

def test_spider_counts_settled_volume_and_equalizes():
    agent = _agent('spider_vol', spider_vol_wei=100)
    agent.observe(1, 0, 1000)
    assert agent.tick(10, [0, 400, 0, 0]) == []
    agent.record_success(100)
    assert agent.tick(11, [0, 400, 0, 0])
    assert spider_plans([0, 400, 200]) == [(1, 0, 200)]

def test_pnp_prefunding_bernstein_and_fixed_budget():
    plan = pnps_plan([[10, 2], [20, 4]], 100, .05)
    assert plan['minimum'] == [20, 4]
    assert plan['target'] == [58, 42] and sum(plan['target']) == 100
    assert pnps_plan([[3, 1], [3, 1]], 4)['minimum'] == [3, 1]
    with pytest.raises(ValueError, match='infeasible'):
        pnps_plan([[10, 2], [20, 4]], 23)
    with pytest.raises(ValueError, match='before funding'):
        pnps_target([0, 100], [1, 3])
    agent = _agent('pnps_static')
    agent.observe(1, 0, 1000)
    assert agent.tick(10, [0, 400, 0, 0]) == [] and agent.events_opened == 0

def test_qp_capacity_counterexample_and_exact_wei_conservation():
    assert overpass_target([0, 100], [9, 1], 'demand', capacities=[60, 60]) == [60, 40]
    assert overpass_target([0, 100], [9, 1], 'demand', q_min=.9) == [90, 10]
    huge = overpass_target([0, 720*ETH+7, 0], [9, 1, 0], 'demand', capacities=[300*ETH]*3)
    assert sum(huge) == 720*ETH+7 and max(huge) <= 300*ETH
    with pytest.raises(ValueError, match='infeasible'):
        overpass_target([0, 100], [9, 1], 'demand', capacities=[20, 20])

@pytest.mark.parametrize('weights', [[1, 1, 1], [1, 2, 4]])
def test_qp_matches_exhaustive_discrete_optimum(weights):
    target = [6, 3, 1]
    got = overpass_target([0, 0, 10], target, 'demand', capacities=[4, 8, 8], weights=weights)
    cost = lambda x: sum(w*(v-t)**2 for w,v,t in zip(weights,x,target))
    feasible = [x for x in itertools.product(range(5), range(9), range(9)) if sum(x)==10]
    assert cost(got) == min(map(cost, feasible))

def test_loopmax_does_not_treat_attempts_or_relay_as_broker_balance_flow():
    agent = _agent('loopmax', valve_init_wei=50)
    agent.observe(1, 0, 1000, src_shard=1)
    assert agent._inflow_demand(2) == [0]*4
    agent.record_success(100, 2, 0, 1)
    assert agent._inflow_demand(2) == [0, 100, 0, 0]

def test_existing_policies_are_untouched_by_the_new_observations():
    """回归：把 src_shard 一起喂进来，topup 的计划必须逐位不变。"""
    old, new = _agent(TdrAgent.POLICY_TOPUP, q_min=0.3, epsilon=0.95), \
        _agent(TdrAgent.POLICY_TOPUP, q_min=0.3, epsilon=0.95)
    for b in range(10):
        old.observe(b, dst_shard=1, value=10 * ETH)
        new.observe(b, dst_shard=1, value=10 * ETH, src_shard=3)
    assert old.tick(20, [0, 100 * ETH, 0, 0]) == new.tick(20, [0, 100 * ETH, 0, 0])
    assert old._flowed_wei == new._flowed_wei == 0 and not old._tracks_inflow


def test_a_lost_move_always_carries_its_measured_burn_block():
    """损耗台账的锚点纪律：已确认 burn 的搬运判失时必须给出 burn 块高，
    否则对账（G3a）无法把那笔链上 burn 记到正确的一行。"""
    agent = _agent(TdrAgent.POLICY_FLEXICHAIN)
    for b in range(10):
        agent.observe(b, dst_shard=1, value=10 * ETH, src_shard=0)
    plans = agent.start_rescue(20, [0, 200 * ETH, 0, 0], 0, 50 * ETH)
    assert plans
    dst_shard = plans[0][1]
    src_shard = plans[0][0]
    amount = plans[0][2]
    agent.mark_first_submitted(0, 20)
    agent.mark_first_confirmed(0)
    agent.mark_second_submitted(0)
    # 引擎漏报块高 ⇒ 拒绝记账（宁可不落账，也不许编一个块高）
    with pytest.raises(ValueError, match="burn 块高"):
        agent.fail(0, "second_failed")
    agent.fail(0, "second_failed", 33)
    assert agent.lost_moves == [(src_shard, dst_shard, amount, 33)]
    assert agent.transfers_lost == 1
    # 迟到的 mint 落账后回冲：按前三个字段配对，块高不参与匹配
    agent.recover_lost(src_shard, dst_shard, amount)
    assert agent.lost_moves == [] and agent.transfers_lost == 0
