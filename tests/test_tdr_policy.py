"""tdr_policy 七组断言 + Go 交叉样例（PLAN §M2/M3：镜像 Go 的验收测试）。

Go 样例来源：pkg/tdr/tdr_test.go 逐条移植（输入输出与断言完全一致），
其余为 PLAN 锁定的七组：Στ=ρ/地板、触发（双模式）、Lemma-1 上界、
搬运量=½‖β−τ‖₁、恢复性、确定性、零需求保持。
"""
import pytest

from brokerlab.tdr_policy import (DemandWindow, EwmaDemand, base_target,
                                  compute_deviation, compute_transfers,
                                  compute_target, percent_of, topup_plans,
                                  valve_plans)

GWEI = 10 ** 9


# ---------------------------------------------------------------------------
# ① Go 交叉样例（tdr_test.go 逐条移植）
# ---------------------------------------------------------------------------

def test_go_sliding_window():
    """TestSlidingWindow：W=3，两块观测在 h=3 计 2、h=4 计 1。"""
    w = DemandWindow(3)
    w.observe(1, 1, 10)
    w.observe(3, 1, 20)
    lam, total = w.demand(3, 2)
    assert total == 30 and w.count(3) == 2
    assert w.count(4) == 1                     # 块高 1 的需求过期滑出


def test_go_window_ready_with_offset():
    """TestWindowReadyWithDeterministicOffset：first@5，W=3，offset=2。"""
    w = DemandWindow(3)
    w.observe(5, 1, 10)
    assert not w.ready(8, offset_blocks=2)
    assert w.ready(9, offset_blocks=2)


def test_go_cold_start_gate():
    """TestAgentWaitsForCompleteColdStartWindow：first@7，W=50 → h=55 未熟，56 熟。"""
    w = DemandWindow(50)
    assert w.first_block is None and not w.ready(100)
    w.observe(7, 1, 10)
    assert not w.ready(55)
    assert w.ready(56)


def test_go_demand_target_and_largest_first():
    """TestDemandTargetAndLargestFirst：actual[30,10,20] demand[0,3,1]
    → τ[0,45,15]；T=[(0→1,30),(2→1,5)]。"""
    actual = [30, 10, 20]
    tau = compute_target(actual, [0, 3, 1])
    assert tau == [0, 45, 15]
    plans = compute_transfers(actual, tau, 0.1)
    assert plans == [(0, 1, 30), (2, 1, 5)]


def test_go_threshold_triggers_complete_set_T():
    """TestThresholdTriggersCompleteSetT：[121,89,90] vs [100,100,100]，ε=0.1
    → 2 笔 (11,10)，执行后精确回到 τ（含死区内的对手方——T 覆盖全部 gap）。"""
    actual, tau = [121, 89, 90], [100, 100, 100]
    plans = compute_transfers(actual, tau, 0.1)
    assert [p[2] for p in plans] == [11, 10]
    post = list(actual)
    for s, d, a in plans:
        post[s] -= a
        post[d] += a
    assert post == tau


def test_go_deadband_no_rebalance():
    """TestThresholdDeadbandDoesNotRebalance：[109,91,100] 无 excess 越出死区 → 不动。"""
    assert compute_transfers([109, 91, 100], [100, 100, 100], 0.1) == []


def test_go_sparse_demand_closed_pool():
    """TestSparseDemandUsesPaperTargetAndKeepsClosedPool：eligible={0,1}，
    shard2 的 100 不许进池；0→1 搬 10；下一块需求过期 → τ=β。"""
    actual = [10, 10, 100]
    tau = compute_target(actual, [0, 10, 0], eligible=[0, 1])
    assert tau == [0, 20, 100]
    plans = compute_transfers(actual, tau, 0.1)
    assert plans == [(0, 1, 10)]


# ---------------------------------------------------------------------------
# ② PLAN 七组断言（Go 样例未覆盖的部分）
# ---------------------------------------------------------------------------

def test_conservation_and_floor():
    """Στ=ρ 恒成立；q_min 地板保证每 eligible 分片 ≥ ρ·q_min/n。"""
    actual = [1000 * GWEI, 0, 500 * GWEI]
    for demand in ([1, 0, 0], [0, 0, 10], [7, 3, 1], [0, 1, 0]):
        tau = compute_target(actual, demand, q_min=0.10)
        assert sum(tau) == sum(actual)
    # 需求全集中在 shard1 → 无地板时 τ[2]=0；q_min=10% ⇒ 至少 ρ·10%/3
    rho = sum(actual)
    tau = compute_target(actual, [0, 100, 0], q_min=0.10)
    assert sum(tau) == rho
    assert tau[0] >= rho * 1000 // (3 * 10000)          # BPS 地板（向下取整界）
    assert tau[2] >= rho * 1000 // (3 * 10000)


def test_trigger_modes_differ_on_shortfall_only():
    """只有短缺越出死区：excess_only 不触发（Go 语义）；two_sided 触发。"""
    # 纯短缺越出死区（89 < 90），超配全在死区内（105,106 ≤ 110）
    actual, tau = [105, 89, 106], [100, 100, 100]
    assert compute_transfers(actual, tau, 0.1, "excess_only") == []
    assert compute_transfers(actual, tau, 0.1, "two_sided") != []


def test_lemma1_transfer_bound():
    """|T| ≤ n−1 且 = max(|S+|,|S−|)。"""
    actual, tau = [500, 0, 0, 0], [100, 100, 150, 150]
    plans = compute_transfers(actual, tau, 0.0)
    assert len(plans) <= 3                       # n−1
    surplus = [i for i in range(4) if actual[i] > tau[i]]
    deficit = [i for i in range(4) if actual[i] < tau[i]]
    assert len(plans) == max(len(surplus), len(deficit))


def test_transfer_volume_equals_half_l1():
    """搬运总量 = ½‖β−τ‖₁（Lemma 1 推论，守恒的同义）。"""
    actual, tau = [30, 10, 20], [0, 45, 15]
    plans = compute_transfers(actual, tau, 0.0)
    assert sum(a for _, _, a in plans) == compute_deviation(actual, tau) // 2


def test_recovery_all_targets_met():
    """触发后执行全部 plans ⇒ 每个子账户精确回到 τ（恢复性）。"""
    import random as _r
    rng = _r.Random(42)
    for trial in range(50):
        n = rng.randrange(2, 7)
        actual = [rng.randrange(0, 500) for _ in range(n)]
        rho = sum(actual)
        if rho == 0:
            continue
        lam = [rng.randrange(0, 100) for _ in range(n)]
        tau = compute_target(actual, lam, q_min=rng.choice([0.0, 0.05, 0.2]))
        plans = compute_transfers(actual, tau, 0.1)
        post = list(actual)
        for s, d, a in plans:
            post[s] -= a
            post[d] += a
        if plans:
            assert post == tau, (trial, actual, tau, plans)   # 触发⇒精确回到 τ
        else:
            # 未触发（excess_only）⇒ 没有任何 excess 越出死区上界；Στ=ρ 恒查
            assert sum(tau) == sum(actual)
            assert all(b <= (tau[i] + percent_of(tau[i], 0.1))
                       for i, b in enumerate(actual)), (trial, actual, tau)


def test_determinism_pure_functions():
    """同样输入永远同样输出（纯函数 + stable 排序 + 定点整数）。"""
    actual = [777, 123, 999, 1]
    lam = [3, 9, 0, 2]
    runs = {tuple(compute_target(actual, lam, q_min=0.15)) for _ in range(5)}
    assert len(runs) == 1
    tau = next(iter(runs))
    runs2 = {tuple(tuple(p) for p in compute_transfers(actual, tau, 0.1))
             for _ in range(5)}
    assert len(runs2) == 1


def test_zero_demand_preserves_allocation():
    """Σλ=0 → τ=β 原样（Go：zero demand sum preserves current allocation）。"""
    actual = [5, 9, 2]
    assert compute_target(actual, [0, 0, 0]) == actual
    assert compute_target(actual, [0, 0, 0], q_min=0.5) == actual


def test_margin_integer_path():
    """margin 与 Go percentOf 逐位一致（ε·1e6 截断 → τ·scaled//1e6）。"""
    assert percent_of(100, 0.1) == 10
    assert percent_of(100000000000000000, 0.07) == 7000000000000000
    assert percent_of(0, 0.1) == 0
    # ε=0.1, τ=100：109 不越出死区（<110 不算越），110 恰在死区边界不越（> 才触发）
    assert compute_transfers([110, 90], [100, 100], 0.1) == []
    assert compute_transfers([111, 89], [100, 100], 0.1) != []


def test_relay_never_self_transfer():
    """from==to 不出现（双指针结构上不可能：同 shard 不会同时在两侧）。"""
    assert all(s != d for s, d, _ in
               compute_transfers([500, 0, 300, 0], [100, 200, 100, 400], 0.0))


# ---------------------------------------------------------------------------
# 傻瓜水位阀（valve）—— 消融对照策略
# ---------------------------------------------------------------------------


def test_valve_no_trigger_below_cap():
    """无分片达到 init×cap ⇒ 不动（[]）。"""
    assert valve_plans([149, 150, 150, 151], 150, 2.0) == []
    assert valve_plans([299, 1, 0, 0], 150, 2.0) == []   # 299 < 300：还差 1


def test_valve_trigger_at_cap_fills_lowest_only():
    """最满者超 cap：只把多余补到【低于 init】的分片（水往低处流），
    不低于 init 的分片分文不动。"""
    plans = valve_plans([300, 150, 80, 60], 150, 2.0)
    # 300-150=150 可补；needy = shard2(80), shard3(60)，缺口最大优先
    assert plans == [(0, 3, 90), (0, 2, 60)]   # 60 差 90、80 差 70 → 补 90+60
    srcs = {p[0] for p in plans}
    assert srcs == {0}                          # 只从最满者流出
    amt = sum(p[2] for p in plans)
    assert amt == 150                           # 恰好搬走超出 init 的量


def test_valve_limited_by_surplus():
    """needy 总缺口 > 可补量时，按缺口顺序补到钱尽。"""
    plans = valve_plans([300, 150, 120, 0], 150, 2.0)
    assert sum(p[2] for p in plans) == 150      # 可补仅 150
    # needy: shard3(0) 缺口 150 最先、shard2(120) 缺口 30
    assert plans == [(0, 3, 150)]


def test_valve_cap_mult_sensitive():
    """cap_mult 决定水位；同分布下 cap_mult=1.5 提前触发、2.0 不触发。"""
    bal = [220, 150, 100, 130]                         # 220 ≥ 150×1.5=225? no
    assert valve_plans(bal, 150, 1.5) == []
    bal2 = [230, 150, 100, 130]                        # 230 ≥ 225 ⇒ 触发
    plans = valve_plans(bal2, 150, 1.5)
    assert plans and {p[0] for p in plans} == {0}
    # 低于 150 的缺口：shard2 差 50、shard3 差 20，合计 70——只能搬出 70
    assert sum(p[2] for p in plans) == 70
    assert plans == [(0, 2, 50), (0, 3, 20)]


# ---------------------------------------------------------------------------
# base-stock 缓冲线（base_target）
# ---------------------------------------------------------------------------


def test_base_target_scales_with_rate():
    """需求大的分片缓冲线高；零需求分片只留地板。"""
    # W=10，lead=6，safety=2：rate=λ/W ETH/块，S=rate×6×2
    # λ=[100,0,0,0] over W=10 → rate=[10,0,0,0] → S=[120,0,0,0]
    line = base_target([50, 150, 150, 50], [100, 0, 0, 0],
                       window_blocks=10, lead_blocks=6.0, safety=2.0)
    assert line[0] == 120
    assert line[1] == line[2] == line[3] == 0


def test_base_target_floor():
    """floor_frac>0 时任何分片都有地板（防放空后突来一笔）。"""
    actual = [100, 100, 100, 100]          # ρ=400, floor = 400×0.05/4 = 5
    line = base_target(actual, [0, 0, 0, 0], window_blocks=10,
                       lead_blocks=6.0, safety=2.0, floor_frac=0.05)
    assert all(s >= 5 for s in line)
    assert all(s <= 5 for s in line)       # 零需求 ⇒ 全地板


def test_base_target_sums_not_forced():
    """base-stock 不强制 ΣS=ρ：钱多就闲置，不硬塞给不需要的分片。"""
    actual = [400, 100, 50, 50]            # ρ=600
    line = base_target(actual, [200, 0, 0, 0], window_blocks=10,
                       lead_blocks=4.0, safety=1.0)
    assert sum(line) < sum(actual)         # 只有热分片需要 80，其余 0


def test_base_transfer_uses_excess_only_semantics():
    """线=base line；某分片高于自己的线 + 另一分片低于自己的线才动。"""
    actual = [200, 40, 90, 90]             # dst0 高于线、dst1 低于线
    line = base_target(actual, [150, 150, 0, 0], window_blocks=10,
                       lead_blocks=6.0, safety=1.0)   # rate=[15,15,0,0]
    # line = [90, 90, 0, 0] → actual=[200,40,90,90]
    # surplus 全在 shard0（110 超线），deficit shard1（50 缺）→ 搬 50
    plans = compute_transfers(actual, line, 0.0, "excess_only")
    assert plans == [(0, 1, 50)]


# ---------------------------------------------------------------------------
# topup_plans —— 需求份额目标 × 只补不下抽
# ---------------------------------------------------------------------------


def test_topup_only_fills_deficit():
    """只补缺口、盈余闲置：target=[100,100], actual=[150,50] → 只搬 50 不抽 150。"""
    plans = topup_plans([150, 50], [100, 100], 0.0)
    assert plans == [(0, 1, 50)]


def test_topup_does_not_drain_surplus_below_target():
    """actual=[200,10], target=[100,100]：只搬 90（缺多少补多少），不把 200 抽到 100。"""
    plans = topup_plans([200, 10], [100, 100], 0.0)
    assert plans == [(0, 1, 90)]


def test_topup_empty_when_no_deficit():
    assert topup_plans([120, 100], [100, 100], 0.0) == []   # 全在线上
    assert topup_plans([80, 80], [100, 100], 0.0) == []     # 全缺但无盈余


def test_topup_partial_when_surplus_insufficient():
    """盈余不够就把缺口补一部分（不硬凑）。"""
    plans = topup_plans([120, 50, 50], [100, 100, 100], 0.0)
    # 盈余仅 shard0=20；缺 shard1=50, shard2=50 → 只能补 20
    assert sum(p[2] for p in plans) == 20


# ---------------------------------------------------------------------------
# EwmaDemand —— 指数平滑需求（τ 稳定性实验）
# ---------------------------------------------------------------------------


def test_ewma_accumulates_and_decays():
    """新值叠加；随块流逝旧值指数衰减，无硬窗口掉出突变。"""
    e = EwmaDemand(half_life_blocks=10.0, min_blocks=1)
    e.observe(1, 0, 100)
    lam, tot = e.demand(1, 2)
    assert lam[0] > 0 and tot > 0
    lam2, tot2 = e.demand(11, 2)
    assert 0.4 < lam2[0] / max(lam[0], 1) < 0.7


def test_ewma_ready_requires_min_blocks():
    e = EwmaDemand(half_life_blocks=10.0, min_blocks=3)
    assert not e.ready(0)
    e.observe(1, 0, 10)
    assert not e.ready(1)
    e.observe(2, 0, 10)
    e.observe(3, 0, 10)
    assert e.ready(3)


def test_ewma_multiple_shards_independent():
    e = EwmaDemand(half_life_blocks=100.0, min_blocks=1)
    e.observe(1, 0, 100)
    e.observe(1, 1, 300)
    lam, tot = e.demand(1, 4)
    assert lam[0] > 0 and lam[1] > 0 and lam[2] == 0
    assert abs(lam[1] / lam[0] - 3.0) < 1e-6


def test_ewma_observe_blocks_updates_nblocks_once_per_block():
    e = EwmaDemand(half_life_blocks=10.0, min_blocks=2)
    e.observe(5, 0, 10)
    e.observe(5, 1, 10)
    e.observe(6, 0, 10)
    assert e._n_blocks == 2
    assert e.ready(6)


def test_ewma_late_observation_is_order_independent():
    """迟到事件按年龄折算到最大块高，且不能倒拨时间锚。"""
    ordered = EwmaDemand(half_life_blocks=10.0, min_blocks=1)
    late = EwmaDemand(half_life_blocks=10.0, min_blocks=1)
    events = [(8, 0, 50), (10, 1, 100), (11, 0, 25)]
    for event in events:
        ordered.observe(*event)
    for event in (events[1], events[0], events[2]):
        late.observe(*event)
    assert late._last_block == 11
    assert late.demand(12, 2) == ordered.demand(12, 2)


def test_ewma_ready_honors_offset_and_distinct_blocks():
    e = EwmaDemand(half_life_blocks=10.0, min_blocks=2)
    e.observe(5, 0, 10)
    e.observe(5, 1, 10)  # 同一块不能重复计冷启动块数
    assert not e.ready(99, offset_blocks=2)
    e.observe(6, 0, 10)
    assert not e.ready(7, offset_blocks=2)
    assert e.ready(8, offset_blocks=2)


def test_topup_can_use_separate_surplus_threshold():
    # deficit epsilon=.5：dst1 在 40<50 时缺；source epsilon=.1：111>110 可供。
    assert topup_plans([111, 40], [100, 100], 0.5, 0.1) == [(0, 1, 11)]
    # 若供钱侧也沿用 .5，则 111 不够 150，不能供钱。
    assert topup_plans([111, 40], [100, 100], 0.5) == []
