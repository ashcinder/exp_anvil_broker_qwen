"""exp033 SOTA 对照臂：纯函数决策规则的逐条单测 + 代理分派单测。

这里断言的是"我方按文献规则实现"这一事本身（触发/目标/幅度三条），
不是性能结论——性能结论只认 exp033 的实测产物。
"""
import pytest

from brokerlab.sota_policies import (autoloop_plans, flexichain_plans,
                                     level_plans, loopmax_plans,
                                     overpass_target, pnps_target,
                                     spider_plans, ucrb_plans, uniform_level,
                                     uniform_target)
from brokerlab.tdr_engine import TdrAgent

ETH = 10 ** 18


def _sum(plans, key):
    return sum(p[key] for p in plans)


# ---------------------------------------------------------------------------
# 公共零件
# ---------------------------------------------------------------------------

def test_uniform_level_and_target_split_the_pool_evenly():
    actual = [500, 100, 0, 400]
    assert uniform_level(actual) == 250
    target = uniform_target(actual)
    assert target == [250, 250, 250, 250]      # Στ=ρ
    assert sum(target) == sum(actual)
    assert uniform_target([7, 0, 0]) == [3, 2, 2]   # 余数归分片 0，不丢钱


def test_level_plans_fill_deficits_and_never_over_draw():
    # 缺口 300（分片 0），盈余各 100（分片 1、2）⇒ 只能补 200，缺口欠着
    assert level_plans([0, 400, 400], 300) == [(1, 0, 100), (2, 0, 100)]
    assert level_plans([100, 100, 100], 300) == []    # 无盈余 ⇒ 不动
    assert level_plans([300, 300], 300) == []         # 恰在水位 ⇒ 不动


# ---------------------------------------------------------------------------
# flexichain：静态均分 + 只补缺口
# ---------------------------------------------------------------------------

def test_flexichain_tops_up_to_the_equal_share_only():
    plans = flexichain_plans([0, 0, 600])           # 水位 200
    assert sorted(plans) == [(2, 0, 200), (2, 1, 200)]
    assert flexichain_plans([250, 250, 250]) == []  # 恰在均分线 ⇒ 不动
    # 均分线看不出需求：热点分片与其他分片同等待遇，这就是它的"目标不看需求"
    assert flexichain_plans([0, 900]) == [(1, 0, 450)]


# ---------------------------------------------------------------------------
# loopmax：预测触发 + 搬满
# ---------------------------------------------------------------------------

def test_loopmax_is_silent_until_a_shard_is_predicted_to_drain():
    # 分片 0 余额 100、每块流出 10 ⇒ ETTD=10 块 < horizon 30 ⇒ 触发
    plans = loopmax_plans([100, 900], outflow=[100, 0], inflow=[0, 100],
                          q_min=0.0, effective_window_blocks=10.0,
                          horizon_blocks=30.0, safety_blocks=0.0)
    assert plans and all(to == 0 for _, to, _ in plans)
    # 同样的失衡，但流出速率慢到 ETTD > horizon ⇒ 不触发（这就是"预测型"）
    assert loopmax_plans([100, 900], outflow=[1, 0], inflow=[0, 1],
                         q_min=0.0, effective_window_blocks=10.0,
                         horizon_blocks=30.0, safety_blocks=0.0) == []


def test_loopmax_moves_the_full_gap_where_tdr_would_hold_a_deadband():
    # 两片都按每块 10 流出；分片 0 ETTD=30<40 触发、分片 1 ETTD=70 不触发。
    # 目标 τ ∝ λ = [500,500] ⇒ 分片 0 从 300 补到 500（全量 200，无死区）。
    plans = loopmax_plans([300, 700], outflow=[100, 100], inflow=[0, 0],
                          q_min=0.0, effective_window_blocks=10.0,
                          horizon_blocks=40.0, safety_blocks=0.0)
    assert plans == [(1, 0, 200)]
    # 安全边际抬的是"预计枯竭"那一片的目标 ⇒ 搬运量只增不减（搬满）
    more = loopmax_plans([300, 700], outflow=[100, 100], inflow=[0, 0],
                         q_min=0.0, effective_window_blocks=10.0,
                         horizon_blocks=40.0, safety_blocks=3.0)
    assert sum(a for _, to, a in more if to == 0) == 200    # 受盈余上限封顶
    assert more == [(1, 0, 200)]


# ---------------------------------------------------------------------------
# spider-vol / autoloop / overpass / pnps / ucrb
# ---------------------------------------------------------------------------

def test_spider_equalizes_the_whole_pool():
    plans = spider_plans([0, 400, 200])                 # 水位 200
    assert plans == [(1, 0, 200)]                       # waterfilling 送往最缺的一方
    assert {to for _, to, _ in plans} == {0}


def test_autoloop_only_moves_when_the_band_is_crossed():
    assert autoloop_plans([40 * ETH, 50 * ETH], 50 * ETH) == []   # 带内 ⇒ 不动
    # 穿带 ⇒ 动到 band 中点 0.5·C = 25 ETH；分片 0 只差 15 ETH
    assert autoloop_plans([10 * ETH, 90 * ETH], 50 * ETH) == [(1, 0, 15 * ETH)]


def test_overpass_theta_selects_the_target_it_is_the_joint_arm():
    actual = [0, 100, 300]
    assert overpass_target(actual, [1, 1, 1], "uniform") == uniform_target(actual)
    demand = overpass_target(actual, [0, 0, 8], "demand")            # θ∝λ ⇒ 全给分片 2
    assert demand[2] == sum(actual)


def test_ucrb_moves_exactly_the_reported_deficit_from_the_richest_source():
    # 分片 0 缺 300；源按余额高者先取，且取到够为止（Σ移位 == 缺口，不够就欠着）
    assert ucrb_plans([0, 100, 50], {0: 300}) == [(1, 0, 100), (2, 0, 50)]
    assert ucrb_plans([0, 500, 50], {0: 300}) == [(1, 0, 300)]
    assert ucrb_plans([900, 100], {}) == []        # 没有失败回报 ⇒ 永不动


def test_pnps_target_is_a_demand_share_like_tdr_but_fires_once():
    assert pnps_target([0, 100], [1, 3], q_min=0.0) == [25, 75]


# ---------------------------------------------------------------------------
# 代理分派：只回答"何时动"，并保证既有策略不被新状态影响
# ---------------------------------------------------------------------------

def _agent(policy, **kw):
    return TdrAgent(4, window_blocks=10, chi_blocks=0, offset_blocks=0,
                    policy=policy, demand_ewma_half_life=10.0, **kw)


def test_pnps_fires_once_then_freezes_forever():
    agent = _agent(TdrAgent.POLICY_PNPS, q_min=0.0)
    for b in range(10):
        agent.observe(b, dst_shard=1, value=10 * ETH, src_shard=0)
    first = agent.tick(20, [200 * ETH, 0, 0, 0])
    # 首个窗口的全部需求都落在分片 1 ⇒ 一次性把整池搬到分片 1（静态规划的极端）
    assert first == [(0, 1, 200 * ETH)] and agent._pnps_done
    agent.tick(40, [0, 0, 200 * ETH, 0])           # 再失衡也不动（已冻结）
    assert agent.events_opened == 1


def test_spider_fires_on_flow_volume_not_on_imbalance():
    agent = _agent(TdrAgent.POLICY_SPIDER, spider_vol_wei=100 * ETH)
    assert agent.tick(5, [0, 0, 300 * ETH, 0]) == []      # 失衡但流水未到 ⇒ 不动
    for b in range(10):
        agent.observe(b, dst_shard=1, value=20 * ETH, src_shard=0)
    assert agent.tick(20, [0, 0, 300 * ETH, 0])           # 累计 200 ETH ≥ 100 ⇒ 触发


def test_loopmax_only_reads_src_side_when_the_policy_asks_for_it():
    agent = _agent(TdrAgent.POLICY_LOOPMAX, q_min=0.0)
    assert agent._inflow_window is not None
    for b in range(10):
        agent.observe(b, dst_shard=2, value=30 * ETH, src_shard=0)
    assert agent._inflow_ewma._rate.get(0, 0) > 0         # 流入侧已按 src 记账
    assert agent.tick(20, [0, 0, 5 * ETH, 400 * ETH])     # ETTD 极短 ⇒ 立刻触发


def test_ucrb_reuses_the_engine_reported_gap():
    agent = _agent(TdrAgent.POLICY_UCRB)
    assert agent.tick(5, [400 * ETH, 0, 0, 0]) == []      # 没失败回报 ⇒ 不动作
    agent.notify_shortfall(1, 5 * ETH)
    assert agent.tick(6, [400 * ETH, 0, 0, 0]) == [(0, 1, 5 * ETH)]


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
    plans = agent.tick(20, [0, 200 * ETH, 0, 0])
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
