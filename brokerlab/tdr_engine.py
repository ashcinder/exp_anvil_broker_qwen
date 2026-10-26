"""TdrAgent —— 一个 broker 钱包的 TDR 决策与事件生命周期（tdr_policy 之上的状态机）。

职责边界：本类不碰链。engine 喂给它「当前块高、账本可用额快照、已提交块高回补」，
它吐「本块要执行的再平衡转账」。转账的两段（burn 自签 + coordinator 代铸 mint）
由 broker_engine 执行，执行结果回报回本类的 confirm/fail——闭环成事件状态机：

    IDLE ─(policy 触发 & χ 满足 & 无活跃事件)→ EVENT_ACTIVE
    每笔 transfer：FIRST_SUBMITTED → FIRST_CONFIRMED → SECOND_REQUESTED
                   → DONE | TIMED_OUT/FAILED
    全部段终态 → EVENT_CLOSED → IDLE（χ 计时从事件起点块高开始）

PLAN 锁定的三条纪律在这里成立：
  ① 事件互斥：同一时刻至多一个活跃事件（论文假设 C4）；
  ② χ 块最小间隔：last_event_block + χ ≤ h 才能开新事件；
  ③ 超时不自动重试，未落定的量记入 lost_in_transit_wei，显式可审计。
需求统计走 tdr_policy.DemandWindow：路由时刻记录（含之后被 relay 兜底的），
修复审计 F3 的"需求盲区"。纯逻辑 + 注入时钟，单测不需要 anvil。
"""
import typing as t
import math

from .tdr_policy import (DemandWindow, EwmaDemand, compute_target,
                         compute_transfers, percent_of, valve_plans,
                         base_target, topup_plans, BPS_SCALE, Transfer)
from .sota_policies import (autoloop_plans, flexichain_plans, loopmax_plans,
                            overpass_target, pnps_target, spider_plans,
                            ucrb_plans, uniform_level, uniform_target)

from .sota_native import (rescue_plans, autoloop_plans, loopmax_plans, overpass_target)

class TdrAgent:
    """单 broker 的 TDR 代理。engine_tick 每块调一次；返回待执行转账清单。"""

    ST_IDLE = "IDLE"
    ST_ACTIVE = "EVENT_ACTIVE"
    POLICY_PROPORTIONAL = "proportional"   # λ 比例分配（论文，带需求窗口）
    POLICY_VALVE = "valve"                 # 傻瓜水位阀（消融对照，无需求预测）
    POLICY_BASE = "base"                   # base-stock：按流出速率定缓冲线，需求侧缺货触发
    POLICY_TOPUP_LOW_WATER = "topup_lowwater"  # independent alpha recipient threshold
    POLICY_TOPUP_QFLOOR = "topup_qfloor"  # exp034: q_min recipient floor, epsilon donor ceiling
    POLICY_TOPUP = "topup"                 # 需求份额目标 τ × 只补缺口不下抽（valve 纪律）
    # —— SOTA 对照臂（决策规则与实现见 sota_policies.py）——
    POLICY_FLEXICHAIN = "flexichain"       # 均分初始化；当前支付不足时救援
    POLICY_LOOPMAX = "loopmax"             # ePrint 22/1385：预测型触发 + 搬满
    POLICY_SPIDER = "spider_vol"           # NSDI'20 §7.4：按累计流水量定频，均衡
    POLICY_PNPS = "pnps_static"            # INFOCOM'20：一次性规划，此后永不再平衡
    POLICY_AUTOLOOP = "autoloop"           # ePrint 22/1385：穿 band 才动，动到中点
    POLICY_OVERPASS = "overpass_qp"        # ePrint 24/1526：凸 QP 的 KKT 闭式解
    POLICY_UCRB = "ucrb"                   # ePrint 25/1023：支付失败后反应式补缺

    def __init__(self, num_shards: int, *, window_blocks: int = 10,
                 epsilon: float = 0.10, q_min: float = 0.10,
                 chi_blocks: int = 5, timeout_blocks: int = 5,
                 trigger_mode: str = "excess_only",
                 offset_blocks: int = 0,
                 policy: str = POLICY_PROPORTIONAL,
                 valve_init_wei: int = 0, valve_cap_mult: float = 2.0,
                 base_lead_blocks: float = 6.0, base_safety: float = 2.0,
                 base_floor_frac: float = 0.05,
                 demand_ewma_half_life: float = 0.0,
                 demand_ewma_min_blocks: int = 5,
                 demand_ewma_window_blocks: int = 0,
                 target_cap: float = 0.0,
                 min_reserve_wei: int = 0,
                 surplus_epsilon: t.Optional[float] = None,
                 topup_two_sided: bool = False,
                 low_water_ratio: float = 0.3,
                 # —— SOTA 对照臂专用旋钮（默认值对既有策略完全无影响）——
                 spider_vol_wei: int = 0,
                 autoloop_low: float = 0.3,
                 autoloop_high: float = 0.7,
                 loopmax_horizon_blocks: float = 0.0,
                 loopmax_safety_blocks: float = 3.0,
                 overpass_theta: str = "uniform",
                 sota_capacity_wei: int = 0,
                 sota_check_blocks: int = 10,
                 sota_conf_blocks: int = 10,
                 qp_capacities=None, qp_weights=None) -> None:
        self.num_shards = int(num_shards)
        # 需求源二选一：ewma_half_life>0 用指数平滑（可选有限滑窗），否则硬窗口。
        self.demand_ewma_half_life = float(demand_ewma_half_life)
        self.window = DemandWindow(window_blocks)
        self.ewma = EwmaDemand(half_life_blocks=float(demand_ewma_half_life)
                               if demand_ewma_half_life > 0 else 20.0,
                               min_blocks=int(demand_ewma_min_blocks),
                               window_blocks=int(demand_ewma_window_blocks))
        self.epsilon = float(epsilon)
        self.q_min = float(q_min)
        self.chi_blocks = int(chi_blocks)
        self.timeout_blocks = int(timeout_blocks)
        self.trigger_mode = trigger_mode
        self.offset_blocks = int(offset_blocks)   # 冷启动错峰（Go WindowReadyWithOffset）
        self.policy = policy
        self.valve_init_wei = int(valve_init_wei)
        self.valve_cap_mult = float(valve_cap_mult)
        self.base_lead_blocks = float(base_lead_blocks)
        self.base_safety = float(base_safety)
        self.base_floor_frac = float(base_floor_frac)
        self.target_cap = float(target_cap)
        self.min_reserve_wei = int(min_reserve_wei)
        self.surplus_epsilon = (self.epsilon if surplus_epsilon is None
                                else float(surplus_epsilon))
        self.low_water_ratio = float(low_water_ratio)
        if not math.isfinite(self.low_water_ratio) or not 0 <= self.low_water_ratio <= 1:
            raise ValueError("low_water_ratio must be finite and in [0, 1]")
        self.topup_two_sided = bool(topup_two_sided)
        if self.policy == self.POLICY_TOPUP_LOW_WATER and (self.topup_two_sided or min_reserve_wei or target_cap):
            raise ValueError("topup_lowwater requires one-sided mode, without absolute reserve or cap")
        if self.policy == self.POLICY_TOPUP_QFLOOR and (self.topup_two_sided or min_reserve_wei or target_cap):
            raise ValueError("topup_qfloor uses q_min alone: disable two-sided, absolute reserve and cap")
        # SOTA 对照臂参数（只在对应 policy 下被读取；其余策略下是死状态）
        self.spider_vol_wei = int(spider_vol_wei)
        self.autoloop_low = float(autoloop_low)
        self.autoloop_high = float(autoloop_high)
        self.loopmax_horizon_blocks = float(loopmax_horizon_blocks)
        self.loopmax_safety_blocks = float(loopmax_safety_blocks)
        self.overpass_theta = str(overpass_theta)
        self.sota_capacity_wei = int(sota_capacity_wei or 2 * valve_init_wei)
        self.sota_check_blocks = int(sota_check_blocks)
        self.sota_conf_blocks = int(sota_conf_blocks)
        self.qp_capacities = qp_capacities
        self.qp_weights = qp_weights
        self._last_policy_check = None
        if self.sota_check_blocks <= 0 or self.sota_conf_blocks < 0:
            raise ValueError("invalid SOTA check/confirmation intervals")
        if not (0.0 <= self.autoloop_low < self.autoloop_high <= 1.0):
            raise ValueError("autoloop band must satisfy 0 <= low < high <= 1")
        if self.overpass_theta not in ("uniform", "demand"):
            raise ValueError("overpass_theta must be 'uniform' or 'demand'")
        if self.spider_vol_wei < 0:
            raise ValueError("spider_vol_wei must be >= 0")
        if self.loopmax_horizon_blocks < 0 or self.loopmax_safety_blocks < 0:
            raise ValueError("loopmax horizons must be >= 0")
        # Topup's epsilon=1 is a valid boundary control: its shortage
        # threshold is zero. Other policies retain the existing domain.
        if self.policy in (self.POLICY_TOPUP_QFLOOR, self.POLICY_TOPUP_LOW_WATER):
            # No epsilon lower-side band: any finite nonnegative upper multiplier is valid.
            if any(not math.isfinite(e) or e < 0 for e in (self.epsilon, self.surplus_epsilon)):
                raise ValueError("q-floor epsilon and surplus_epsilon must be finite and >= 0")
        else:
            epsilon_limit = 1.0 if self.policy == self.POLICY_TOPUP else None
            if not (0 <= self.epsilon < 1 or self.epsilon == epsilon_limit):
                raise ValueError("epsilon must be in [0, 1), or 1 for Topup")
            if not (0 <= self.surplus_epsilon < 1 or self.surplus_epsilon == epsilon_limit):
                raise ValueError("surplus_epsilon must be in [0, 1), or 1 for Topup")
        if not 0 <= self.q_min <= 1:
            raise ValueError("q_min must be in [0, 1]")
        if (self.target_cap < 0 or self.target_cap > 1
                or (0 < self.target_cap < 1 / self.num_shards)):
            raise ValueError("target_cap must be 0 (disabled) or in [1/n, 1]")
        if self.min_reserve_wei < 0:
            raise ValueError("min_reserve_wei must be >= 0")
        self.state = self.ST_IDLE
        self._start_block: t.Optional[int] = None
        self.last_event_block: t.Optional[int] = None
        self.active: t.List[t.Dict[str, t.Object]] = []   # 事件内转账跟踪表
        # 审计计数器（全部显式，供报表与守恒核对）
        self.events_opened = 0
        self.transfers_done = 0
        self.transfers_lost = 0
        self.moved_wei = 0                      # 已完成搬运总量（G3 台账用）
        # burn 成而 mint 失的搬运：(src, dst, amt, burn_block)。
        # 必须带 burn 块高——那笔 burn 已上链，对账（G3a）要把它记到正确的一行；
        # 口径与 submit_regime_shift/scripts/run.py 一致（moves 带 mint 块高）。
        self.lost_moves: t.List[t.Tuple[int, int, int, int]] = []
        self.lost_in_transit_wei = 0
        self.demand_observed_total = 0            # 路由时刻记入的需求笔数（含未服务）
        self.last_plan: t.List[Transfer] = []
        self.last_target: t.Optional[t.List[int]] = None  # 最近一次真实决策的 τ
        # —— SOTA 对照臂的运行状态（只在对应 policy 下被写入/读取）——
        self._tracks_flow = self.policy == self.POLICY_SPIDER
        self._tracks_inflow = self.policy == self.POLICY_LOOPMAX
        self._flowed_wei = 0                       # 累计流水（spider 定频用）
        self._spider_next = self.spider_vol_wei    # 下次触发的水位
        self._pnps_done = self.policy == self.POLICY_PNPS                    # pnps 一次性规划已用掉
        self._pending_shortfall: t.Dict[int, int] = {}   # ucrb：失败缺口待补
        # loopmax 需要"流入"侧估计（src 分片需求）来算净需求 Â_net。
        # 与流出侧同源同参：hl>0 用 EWMA，否则用硬窗口——两边单位必须一致。
        self._inflow_window: t.Optional[DemandWindow] = None
        self._inflow_ewma: t.Optional[EwmaDemand] = None
        self._loop_outflow_window = DemandWindow(self.window.window_blocks)
        self._loop_outflow_ewma = EwmaDemand(
            half_life_blocks=float(demand_ewma_half_life) if demand_ewma_half_life > 0 else 20.,
            min_blocks=int(demand_ewma_min_blocks), window_blocks=int(demand_ewma_window_blocks))
        if self._tracks_inflow:
            self._inflow_window = DemandWindow(self.window.window_blocks)
            self._inflow_ewma = EwmaDemand(
                half_life_blocks=(float(demand_ewma_half_life)
                                  if demand_ewma_half_life > 0 else 20.0),
                min_blocks=int(demand_ewma_min_blocks),
                window_blocks=int(demand_ewma_window_blocks))

    # ------------------------------------------------------------------
    # 需求侧：engine 在 CTX 的 Θ1 提交块高回补时调用（路由时刻语义）
    # ------------------------------------------------------------------
    def observe(self, block: int, dst_shard: int, value: int,
                src_shard: t.Optional[int] = None) -> None:
        self.window.observe(block, dst_shard, value)
        if self.demand_ewma_half_life > 0:
            self.ewma.observe(block, dst_shard, value)
        if value > 0:
            self.demand_observed_total += 1
            # SOTA 臂的附加统计（对既有策略无副作用）：
            #   spider：累计流水量定频；loopmax：流入侧（src 分片）需求估计。
    def record_success(self, value: int, block=0, dst_shard=None, src_shard=None) -> None:
        if self._tracks_flow and value > 0:
            self._flowed_wei += int(value)
        if self._tracks_inflow and value > 0 and src_shard is not None and dst_shard is not None:
            self._inflow_window.observe(block, src_shard, value)
            self._inflow_ewma.observe(block, src_shard, value)
            self._loop_outflow_window.observe(block, dst_shard, value)
            self._loop_outflow_ewma.observe(block, dst_shard, value)

    def start_rescue(self, block, available, shard, amount):
        if self.policy not in (self.POLICY_FLEXICHAIN, self.POLICY_UCRB):
            return []
        if self.state != self.ST_IDLE:
            return []
        gap = max(0, int(amount) - int(available[shard]))
        return self.open_plan(block, rescue_plans(available, {shard: gap}))

    def notify_shortfall(self, shard: int, value: int) -> None:
        """ucrb 的反应式输入：终审改判 relay 时引擎回报缺口。其余策略忽略。"""
        if self.policy != self.POLICY_UCRB or value <= 0:
            return
        self._pending_shortfall[int(shard)] = (self._pending_shortfall.get(int(shard), 0)
                                               + int(value))

    def _eff_window(self) -> float:
        """需求估计的等效窗口长度（块）：EWMA 用指数核等效长度，否则用硬窗口。"""
        if self.demand_ewma_half_life > 0:
            return float(self.ewma.effective_window_blocks)
        return float(self.window.window_blocks)

    def _inflow_demand(self, block: int) -> t.List[int]:
        """流入侧需求向量（src 分片），与流出侧同参数同单位；未启用时全 0。"""
        if self._inflow_window is None:
            return [0] * self.num_shards
        if self.demand_ewma_half_life > 0:
            return self._inflow_ewma.demand(block, self.num_shards)[0]
        return self._inflow_window.demand(block, self.num_shards)[0]

    def _req_ready(self, block: int) -> bool:
        """需求源的就绪判断：EWMA 用最小块数，硬窗口用满窗。"""
        if self.demand_ewma_half_life > 0:
            return self.ewma.ready(block, self.offset_blocks)
        return self.window.ready(block, self.offset_blocks)

    def _req_demand(self, block: int) -> t.Tuple[t.List[int], int]:
        if self.demand_ewma_half_life > 0:
            return self.ewma.demand(block, self.num_shards)
        return self.window.demand(block, self.num_shards)

    # ------------------------------------------------------------------
    # 规划侧：块边界调用。available = 账本 available 快照（不含在途进账）。
    # 返回需要执行的转账（空列表 = 未触发/不满足开事件条件）。
    # ------------------------------------------------------------------
    def tick(self, block: int, available: t.Sequence[int]) -> t.List[Transfer]:
        if self._start_block is None:
            self._start_block = int(block)
        if self.state != self.ST_IDLE:
            return []                              # 事件互斥：活跃期不新开
        if (self.last_event_block is not None
                and block < self.last_event_block + self.chi_blocks):
            return []                              # χ 块最小间隔未到
        av = [int(a) for a in available]
        if self.policy in (self.POLICY_FLEXICHAIN, self.POLICY_UCRB, self.POLICY_PNPS):
            return []  # current-payment repair / prefunding, never periodic topup
        if self.policy in (self.POLICY_AUTOLOOP, self.POLICY_LOOPMAX, self.POLICY_OVERPASS):
            if self._last_policy_check is not None and block < self._last_policy_check + self.sota_check_blocks:
                return []
            self._last_policy_check = block
        if self.policy == self.POLICY_VALVE:
            # 傻瓜水位阀：不看需求窗口，余额到 init×cap 才触发。
            # 无需窗口冷启动保护（触发条件本身就是水位），但保留错峰偏移。
            if block < self._start_block + self.offset_blocks:
                return []
            plans = valve_plans(av, self.valve_init_wei, self.valve_cap_mult)
        elif self.policy == self.POLICY_BASE:
            # base-stock：缓冲线按"近期流出速率×lead×safety"缩放（热分片多放）。
            # 仍是需求窗口驱动（速率估计用窗口 λ），但与 proportional 的差别：
            #  ① 目标不是 Στ=ρ 的全量重排，而是每分片独立的缓冲线（多余闲置）；
            #  ② 触发走缺货侧——某分片低于自己缓冲线即补，从高于自己线的分片取。
            if not self._req_ready(block):
                return []                          # 冷启动保护：未满一个完整窗口
            lam, _ = self._req_demand(block)
            eff_w = (self.ewma.effective_window_blocks
                     if self.demand_ewma_half_life > 0
                      else self.window.window_blocks)
            line = base_target(av, lam, eff_w,
                               self.base_lead_blocks, self.base_safety,
                               self.base_floor_frac)
            self.last_target = list(line)
            # excess_only 语义：只有当某个分片【高于自己缓冲线】才动，
            # 动时把盈余喂给低于自己线的分片——天然满足"只从有富余的分片取"。
            plans = compute_transfers(av, line, self.epsilon, "excess_only")
        elif self.policy in (self.POLICY_TOPUP, self.POLICY_TOPUP_QFLOOR, self.POLICY_TOPUP_LOW_WATER):
            # 需求份额目标 τ（proportional 的目标）＋ valve 的只补不下抽纪律。
            # 用 τ 给热分片高位缓冲；但只填低于 τ 的缺口、从高于 τ 的取，
            # 补完即停、盈余闲置——避免 proportional 每次全量重排的搬运浪费。
            if not self._req_ready(block):
                return []
            lam, _ = self._req_demand(block)
            rho = sum(av)
            reserve_q = ((self.min_reserve_wei * self.num_shards / rho)
                         if rho > 0 and self.min_reserve_wei > 0 else 0.0)
            effective_qmin = min(1.0, max(self.q_min, reserve_q))
            tau = compute_target(av, lam, q_min=effective_qmin,
                                 cap=self.target_cap)
            self.last_target = list(tau)
            floor = (rho * int(self.q_min * BPS_SCALE) // (self.num_shards * BPS_SCALE)
                     if self.policy == self.POLICY_TOPUP_QFLOOR else None)
            plans = topup_plans(av, tau, self.epsilon,
                                self.surplus_epsilon,
                                two_sided=self.topup_two_sided,
                                recipient_floor_wei=floor,
                                recipient_ratio=(self.low_water_ratio
                                    if self.policy == self.POLICY_TOPUP_LOW_WATER else None))
        elif self.policy == self.POLICY_LOOPMAX:
            # 预测耗尽/饱和；最大可行请求与安全额，内部配对为平台适配。
            if not self._req_ready(block):
                return []
            lam = (self._loop_outflow_ewma.demand(block, self.num_shards)[0]
                   if self.demand_ewma_half_life > 0 else
                   self._loop_outflow_window.demand(block, self.num_shards)[0])
            horizon = (self.loopmax_horizon_blocks
                       or float(self.sota_check_blocks + self.sota_conf_blocks))
            plans = loopmax_plans(av, lam, self._inflow_demand(block),
                                  self.q_min, self._eff_window(), horizon,
                                  self.loopmax_safety_blocks,
                                  capacities=[self.sota_capacity_wei] * self.num_shards)
        elif self.policy == self.POLICY_SPIDER:
            # 定频：累计流水量到阈值才均衡一次，与真实失衡程度无关。
            if block < self._start_block + self.offset_blocks:
                return []
            if self.spider_vol_wei <= 0 or self._flowed_wei < self._spider_next:
                return []
            self.last_target = uniform_target(av)
            plans = spider_plans(av)
            if plans:
                self._spider_next = self._flowed_wei + self.spider_vol_wei
        elif self.policy == self.POLICY_AUTOLOOP:
            # 穿 band 才动、动到中点（无需求预测）。
            if block < self._start_block + self.offset_blocks:
                return []
            self.last_target = [int((self.autoloop_low + self.autoloop_high)
                                    / 2 * self.sota_capacity_wei)] * self.num_shards
            plans = autoloop_plans(av, self.sota_capacity_wei,
                                   self.autoloop_low, self.autoloop_high)
        elif self.policy == self.POLICY_OVERPASS:
            # Alg.3 周期有界 QP；不混入 Alg.2 阈值或 TDR 地板/死区。
            if not self._req_ready(block):
                return []
            lam, _ = self._req_demand(block)
            tau = overpass_target(av, lam, self.overpass_theta,
                                  capacities=self.qp_capacities, weights=self.qp_weights)
            self.last_target = list(tau)
            plans = compute_transfers(av, tau, 0.0, "excess_only")
        else:
            if not self._req_ready(block):
                return []                          # 冷启动保护：未满一个完整窗口
            lam, _ = self._req_demand(block)
            # proportional 保持论文/旧基线语义；绝对地板和目标上限只属于
            # 修正版 topup，避免把新机制偷偷混进对照臂。
            tau = compute_target(av, lam, q_min=self.q_min)
            self.last_target = list(tau)
            plans = compute_transfers(av, tau, self.epsilon, self.trigger_mode)
        if not plans:
            return []
        return self.open_plan(block, plans)

    def open_plan(self, block, plans):
        if not plans or self.state != self.ST_IDLE:
            return []
        self.state = self.ST_ACTIVE
        self.events_opened += 1
        self.last_event_block = block
        self.last_plan = list(plans)
        self.active = [{"src": s, "dst": d, "amount": a,
                        "stage": "pending", "submitted_block": None,
                        "deadline_block": None}
                       for (s, d, a) in plans]
        return list(plans)

    # ------------------------------------------------------------------
    # 执行回报侧：engine 把链上进展喂回来（块高用引擎可见的最新块）
    # ------------------------------------------------------------------
    def _get(self, idx: int) -> t.Optional[t.Dict[str, t.Any]]:
        """幂等保护：事件已关闭或该笔已终态后，晚到的回报静默忽略。"""
        if idx >= len(self.active) or self.active[idx]["stage"] == "failed":
            return None
        return self.active[idx]

    def mark_first_submitted(self, idx: int, block: int) -> None:
        tr = self._get(idx)
        if tr is None:
            return
        tr["stage"] = "first_submitted"
        tr["submitted_block"] = block
        tr["deadline_block"] = block + self.timeout_blocks

    def mark_first_confirmed(self, idx: int) -> None:
        """burn 段落定（钱已离开 src 子账户进 BURN）——之后 mint 若失败即真损耗。"""
        tr = self._get(idx)
        if tr is None:
            return
        tr["stage"] = "first_confirmed"
        tr["first_confirmed"] = True

    def mark_second_submitted(self, idx: int) -> None:
        """mint 代铸请求已进 coordinator 队列/已签出。"""
        tr = self._get(idx)
        if tr is not None:
            tr["stage"] = "second_requested"

    def mark_second_confirmed(self, idx: int) -> None:
        tr = self._get(idx)
        if tr is None:
            return
        tr["stage"] = "done"
        tr["second_confirmed"] = True
        self.transfers_done += 1
        self.moved_wei += tr["amount"]
        self._maybe_close()

    def fail(self, idx: int, reason: str,
             burn_block: t.Optional[int] = None) -> None:
        """段失败/超时：不重试（PLAN 纪律）。burn 已成而 mint 未成 ⇒ 该笔计入
        lost_in_transit_wei；burn 未成 ⇒ 无资金离开，不计损耗。

        burn_block：引擎在 first_confirmed 时已量到该段的确认块高，必须原样带进来
        ——这笔 burn 已经上链，对账要按它定位，策略层不许自己编块高。"""
        tr = self._get(idx)
        if tr is None:
            return
        already_lost = tr.get("first_confirmed") and not tr.get("second_confirmed")
        # 先校验后落账：拒绝时状态必须原封不动，否则这笔搬运既没记账也再不能被
        # 补记（_get 会把 failed 当终态），链上那笔 burn 就永久失联了。
        if (reason in ("second_failed", "second_timeout") and already_lost
                and burn_block is None):
            raise ValueError(
                "已确认 burn 的搬运判失时必须给出 burn 块高（否则对账无锚点）")
        tr["stage"] = "failed"
        tr["fail_reason"] = reason
        if reason in ("second_failed", "second_timeout") and already_lost:
            self.lost_in_transit_wei += tr["amount"]
            self.transfers_lost += 1
            self.lost_moves.append((tr["src"], tr["dst"], tr["amount"],
                                    int(burn_block)))
        self._maybe_close()

    def recover_lost(self, src: int, dst: int, amount: int) -> None:
        """engine 回报：超时判负的段随后在链上落定——损耗回冲（账本追链）。"""
        self.lost_in_transit_wei = max(0, self.lost_in_transit_wei - amount)
        self.transfers_lost = max(0, self.transfers_lost - 1)
        if self.lost_moves:
            for i, move in enumerate(self.lost_moves):
                if tuple(move[:3]) == (src, dst, amount):
                    self.lost_moves.pop(i)
                    break
        self.transfers_done += 1
        self.moved_wei += amount

    def sweep_timeouts(self, block: int) -> None:
        """超 deadline 的【首段】判超时（engine 判前会先补一次探针防慢落定）。
        第二段不设主动超时：mint 由 coordinator 提交，回执必达（其 relay_timeout
        兜底 status=0）——状态机若抢先记失、engine 后收到成功回执，会双记。"""
        for i, tr in enumerate(self.active):
            if (tr["stage"] == "first_submitted"
                    and tr.get("deadline_block") is not None
                    and block > tr["deadline_block"]):
                self.fail(i, "first_timeout")

    def _maybe_close(self) -> None:
        if self.active and all(tr["stage"] in ("done", "failed")
                               for tr in self.active):
            self.active = []
            self.state = self.ST_IDLE

    # ------------------------------------------------------------------
    def snapshot(self) -> t.Dict[str, t.Any]:
        """报表快照（exp003 的 summary 直接取这个字典）。"""
        return {"events_opened": self.events_opened,
                "transfers_done": self.transfers_done,
                "moved_wei": self.moved_wei,
                "transfers_lost": self.transfers_lost,
                "lost_in_transit_wei": self.lost_in_transit_wei,
                "lost_moves": list(self.lost_moves),
                "demand_observed_total": self.demand_observed_total,
                "state": self.state,
                "last_event_block": self.last_event_block,
                "policy": self.policy,
                # SOTA 对照臂的诊断量（其余策略恒为 0/空，不影响旧口径）
                "flowed_wei": self._flowed_wei,
                "pending_shortfall_wei": sum(self._pending_shortfall.values()),
                "pnps_frozen": self._pnps_done}
