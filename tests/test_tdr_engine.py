"""TdrAgent 状态机单测：互斥、χ、冷启动保护、超时→lost_in_transit。"""
from brokerlab.tdr_engine import TdrAgent


def _agent(**kw):
    a = TdrAgent(3, window_blocks=2, epsilon=0.1, q_min=0.0,
                 chi_blocks=3, timeout_blocks=2, **kw)
    return a


def test_ewma_observation_window_is_configurable():
    a = _agent(policy=TdrAgent.POLICY_TOPUP,
               demand_ewma_half_life=4,
               demand_ewma_min_blocks=3)
    a.observe(1, 0, 100)
    a.observe(2, 0, 100)
    assert a._req_ready(2) is False
    a.observe(3, 0, 100)
    assert a._req_ready(3) is True


def _prime(a, h_first=1):
    """喂满一个窗口的需求 + 失衡快照：shard2 大额超配（相对 τ）。"""
    a.observe(h_first, 0, 100)          # 需求集中在 0/1 → τ 偏向 0,1
    a.observe(h_first + 1, 1, 100)


def test_cold_start_gate_blocks_planning():
    a = _agent()
    _prime(a)
    # W=2, first=1 → h=2 才 ready；ready 之前 tick 永远空
    assert a.tick(1, [10, 10, 180]) == []


def test_trigger_then_exclusive_then_chi():
    a = _agent()
    _prime(a)
    plans = a.tick(2, [10, 10, 180])    # τ=[100,100,0] → shard2 excess 触发
    assert plans and sum(p[2] for p in plans) == 180
    assert a.state == TdrAgent.ST_ACTIVE
    # 事件互斥：活跃期再 tick 不新开
    assert a.tick(9, [10, 10, 10]) == []
    # 正常收尾
    for i in range(len(a.active)):
        a.mark_first_submitted(i, 9)
        a.mark_first_confirmed(i)
        a.mark_second_submitted(i)
        a.mark_second_confirmed(i)
    assert a.state == TdrAgent.ST_IDLE and a.transfers_done == len(plans)
    # χ：last_event_block=2 → h<5 不开新事件
    assert a.tick(4, [10, 10, 180]) == []
    # 喂新需求（旧窗口已过期——滑出是滑窗的正确语义），h=5 χ 到期可再开
    a.observe(4, 0, 100)
    a.observe(5, 1, 100)
    assert a.tick(5, [10, 10, 180]) != []


def test_first_timeout_no_loss_second_failed_is_lost():
    """新语义：sweep 只判 first_timeout（burn 未落定 → 无损耗）；
    第二段失败只来自 coordinator 的显式回执（second_failed）→ 记损耗。"""
    a = _agent()
    _prime(a)
    plans = a.tick(2, [10, 10, 180])
    a.mark_first_submitted(0, 2)
    a.sweep_timeouts(99)                        # 第一笔：burn 没确认就超时
    assert a.lost_in_transit_wei == 0
    if len(a.active) > 1:
        a.mark_first_submitted(1, 2)
        a.mark_first_confirmed(1)
        a.mark_second_submitted(1)
        a.sweep_timeouts(99)                    # 第二段不再被 sweep 判死
        assert a.lost_in_transit_wei == 0
        a.fail(1, "second_failed")              # coordinator 明确回 0 才算损耗
        assert a.lost_in_transit_wei == plans[1][2]
        assert a.transfers_lost == 1
        assert a.lost_moves == [(plans[1][0], plans[1][1], plans[1][2])]
    a.mark_second_submitted(0) if a.active else None
    assert a.state == TdrAgent.ST_IDLE          # 全终态即关事件
    assert a.snapshot()["events_opened"] == 1


def test_demand_includes_relayed_ctx():
    """审计 F3 修复的语义：observe 不问结果——被 relay 的 CTX 也计需求。"""
    a = _agent()
    a.observe(1, 0, 50)                 # 这笔之后全部失败也无妨
    a.observe(2, 1, 50)
    lam, total = a.window.demand(2, 3)
    assert lam == [50, 50, 0] and a.demand_observed_total == 2


def test_late_report_after_timeout_is_ignored():
    """超时判负后晚到的 confirm 静默忽略（幂等），不虚增计数。"""
    a = _agent()
    _prime(a)
    plans = a.tick(2, [10, 10, 180])
    a.mark_first_submitted(0, 2)
    a.sweep_timeouts(99)                       # deadline 过 → first_timeout
    a.mark_first_confirmed(0)                  # 链上回报"迟到了"——active 已关闭/failed
    assert a.snapshot()["transfers_lost"] == 0  # first 未 confirmed，无损耗
    assert a.snapshot()["transfers_done"] == 0


# ---------------------------------------------------------------------------
# 傻瓜水位阀 policy 路径：不看窗口、只认水位
# ---------------------------------------------------------------------------


def test_valve_policy_ignores_window_gate():
    a = _agent(policy=TdrAgent.POLICY_VALVE, valve_init_wei=150,
               valve_cap_mult=2.0)
    # 不喂任何需求、块高还很早：proportional 会被冷启动门挡住，valve 只看水位。
    plans = a.tick(0, [300, 150, 0])       # shard0=300 = 2×150 ⇒ 触发
    assert plans and sum(p[2] for p in plans) == 150
    assert all(p[0] == 0 for p in plans)   # 只从最满者流出
    # 补到 init 为止：shard2 从 0 到 150
    assert sorted((p[1], p[2]) for p in plans) == [(2, 150)]


def test_valve_policy_no_water_no_plan():
    a = _agent(policy=TdrAgent.POLICY_VALVE, valve_init_wei=150,
               valve_cap_mult=2.0)
    assert a.tick(0, [290, 150, 160]) == []   # 290 < 300：水位未满


def test_valve_respects_chi():
    a = _agent(policy=TdrAgent.POLICY_VALVE, valve_init_wei=150,
               valve_cap_mult=2.0)
    plans = a.tick(2, [300, 150, 0])
    assert plans
    for i in range(len(a.active)):
        a.mark_first_submitted(i, 2)
        a.mark_first_confirmed(i)
        a.mark_second_submitted(i)
        a.mark_second_confirmed(i)
    assert a.state == TdrAgent.ST_IDLE
    assert a.tick(4, [300, 150, 0]) == []   # χ：last=2, chi=3 → h<5 不开


# ---------------------------------------------------------------------------
# base-stock policy 路径
# ---------------------------------------------------------------------------


def test_base_policy_uses_line_not_window_full_target():
    a = TdrAgent(3, window_blocks=2, epsilon=0.1, q_min=0.0,
                 chi_blocks=0, timeout_blocks=2,
                 policy=TdrAgent.POLICY_BASE, base_lead_blocks=2.0,
                 base_safety=2.0, base_floor_frac=0.0)
    a.observe(1, 0, 100)          # 需求全在 dst0
    a.observe(1, 1, 100)
    # W=2, lead=2, safety=2 → rate=λ/W, line=rate*2*2
    # 若 available=[10,10,180]: shard0 demand高线高,shard2 无需求线低
    plans = a.tick(2, [10, 10, 180])
    # line0 = (100/2)*4 = 200? 实际 rate=100 over 2 block? window count 100 at block1 each => λ0=100,λ1=100
    # line = [200,200,0]; shard2(180)>0 surplus, 但 shard0/1 都缺 200-10=190 → 补 180
    assert plans and sum(p[2] for p in plans) == 180
    assert a.state == TdrAgent.ST_ACTIVE


# ---------------------------------------------------------------------------
# topup policy 路径
# ---------------------------------------------------------------------------


def test_topup_policy_fills_deficit_only():
    a = TdrAgent(3, window_blocks=2, epsilon=0.0, q_min=0.0,
                 chi_blocks=0, timeout_blocks=2, policy=TdrAgent.POLICY_TOPUP)
    a.observe(1, 0, 100)          # 需求全在 dst0 → τ0 吃掉整个池
    # available=[150,150,0]: 池=300，τ=[300,0,0]。
    # shard0 缺 150（低于 τ0）、shard1 盈余 150（高于 τ1=0）→ 从 1 补 0，只补缺口。
    plans = a.tick(2, [150, 150, 0])
    assert plans == [(1, 0, 150)]
    assert a.state == TdrAgent.ST_ACTIVE
    # 收尾回 IDLE
    for i in range(len(a.active)):
        a.mark_first_submitted(i, 2)
        a.mark_first_confirmed(i)
        a.mark_second_submitted(i)
        a.mark_second_confirmed(i)
    assert a.state == TdrAgent.ST_IDLE
    # 已到 τ：不再动（盈余闲置不抽）
    assert a.tick(4, [300, 0, 0]) == []


def test_topup_applies_absolute_floor_and_target_cap():
    a = TdrAgent(4, window_blocks=1, epsilon=0.0, q_min=0.0,
                 chi_blocks=0, timeout_blocks=2,
                 policy=TdrAgent.POLICY_TOPUP,
                 min_reserve_wei=20, target_cap=0.40)
    a.observe(1, 0, 1000)
    a.tick(1, [25, 25, 25, 25])
    assert a.last_target is not None
    assert min(a.last_target) >= 20
    assert max(a.last_target) <= 40
    assert sum(a.last_target) == 100


def test_valve_offset_is_relative_to_engine_start():
    a = _agent(policy=TdrAgent.POLICY_VALVE, valve_init_wei=150,
               valve_cap_mult=2.0, offset_blocks=3)
    assert a.tick(100, [300, 150, 0]) == []
    assert a.tick(102, [300, 150, 0]) == []
    assert a.tick(103, [300, 150, 0]) != []
