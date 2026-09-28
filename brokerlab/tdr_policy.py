"""TDR 策略纯函数 —— 逐语义镜像 Go 参考实现 pkg/tdr/（PLAN 锁定：矛盾时以 Go 为准）。

对齐清单（源文件在括号里）：
  DemandWindow      按路由时刻记录需求，W 块滑动窗口（agent.go ObserveCTXAt /
                    collectCTXRequests：oldest = h−W+1；确认成功不是计入前提）
  WindowReady       满一个完整窗口才规划，防冷启动风暴（agent.go WindowReady）
  compute_target    Phase A 比例整数分 + 余数归最大 λ（allocation.go）
                    Phase B q_min 地板抬升 + 超额按比例回收 + 末位吸收残差
                    + reconcile 差额归最大 τ。BPS 整数化逐行照抄。
  compute_transfers 死区触发只看 excess；触发后全部正负 gap 参与，
                    降序双指针贪心 min(头,头)，|T| = max(|S+|,|S−|)
                    （reallocation.go computeOptimalTransfers + phase3 注释）。
  margin 取整        ε 先化成 1e6 定点再 τ·scaled//1e6（reallocation.go percentOf）。

本文件无 I/O、无链上动作——M3 引擎在块边界调用它拿计划。
全部金额为 int（wei）。
"""
import typing as t
from dataclasses import dataclass

EPS_SCALE = 10 ** 6     # margin 定点基数（Go percentOf）
BPS_SCALE = 10 ** 4     # 地板/上限定点基数（Go applyConstraints 的 floorBPS/capBPS）


def percent_of(v: int, ratio: float) -> int:
    """margin = τ·ε，整数化路径与 Go percentOf 完全一致：
    scaled=int64(ratio×1e6)（截断！），再 v×scaled//1e6。"""
    if v <= 0 or ratio <= 0:
        return 0
    scaled = int(ratio * EPS_SCALE)
    return v * scaled // EPS_SCALE


# ---------------------------------------------------------------------------
# 需求窗口（agent.go）
# ---------------------------------------------------------------------------

class DemandWindow:
    """W 块滑动需求窗口。observe 允许"补记"：以 CTX 真实提交块高回填，
    因此插入顺序不影响窗口内容（与 Go 的 (h,req) 列表语义一致）。"""

    def __init__(self, window_blocks: int) -> None:
        if window_blocks <= 0:
            raise ValueError("window_blocks must be > 0")
        self.window_blocks = int(window_blocks)
        self._items: t.List[t.Tuple[int, int, int]] = []   # (block, dst_shard, value)
        self.first_block: t.Optional[int] = None

    def observe(self, block: int, dst_shard: int, value: int) -> None:
        """路由时刻记录一笔需求（Go: successful confirmation is not a prerequisite）。
        value≤0 忽略（Go ObserveCTXAt 同语义）。"""
        if value <= 0:
            return
        self._items.append((int(block), int(dst_shard), int(value)))
        if self.first_block is None or block < self.first_block:
            self.first_block = int(block)

    def ready(self, block: int, offset_blocks: int = 0) -> bool:
        """满一个完整 W 窗口（含确定性错峰偏移）才允许规划。"""
        if self.first_block is None:
            return False
        return block >= self.first_block + self.window_blocks - 1 + max(0, offset_blocks)

    def demand(self, block: int, shard_count: int) -> t.Tuple[t.List[int], int]:
        """返回窗口 [block−W+1, block] 内的 λ 向量与总量（顺带丢弃过期项）。"""
        oldest = block - self.window_blocks + 1
        kept = []
        lam = [0] * shard_count
        total = 0
        for (b, d, v) in self._items:
            if b < oldest:
                continue
            kept.append((b, d, v))
            if 0 <= d < shard_count:
                lam[d] += v
                total += v
        self._items = kept
        return lam, total

    def count(self, block: int) -> int:
        oldest = block - self.window_blocks + 1
        return sum(1 for (b, _, _) in self._items if b >= oldest)


class EwmaDemand:
    """按块指数衰减的需求速率估计（τ 稳定性实验用）。

    与 DemandWindow 的差别：硬窗口一笔大额滑出就骤降（τ 突变→追涨杀跌）。
    本估计器无"掉出突变"：每次 observe 把旧累计量按块差指数衰减，
    再叠加新值。衰减因子 d(Δ)=2^(-Δ/half_life)。

    observe 必须允许完成回报乱序：状态始终锚定在见过的最大块高；迟到事件
    先衰减到该锚点再加入，绝不把时间锚倒拨。

    接口与 DemandWindow 对齐：observe(block,dst,value) / ready(block) /
    demand(block,shard_count)→(λ, 总量)。纯逻辑，无 I/O。"""

    def __init__(self, half_life_blocks: float = 20.0,
                 min_blocks: int = 5) -> None:
        if half_life_blocks <= 0:
            raise ValueError("half_life_blocks must be > 0")
        if min_blocks <= 0:
            raise ValueError("min_blocks must be > 0")
        self.half_life = float(half_life_blocks)
        self.min_blocks = int(min_blocks)      # 冷启动：至少见过几块才 ready
        self._rate: t.Dict[int, float] = {}    # shard → 指数衰减需求累计量
        self._last_block: t.Optional[int] = None
        self._first_block: t.Optional[int] = None
        self._seen_blocks: t.Set[int] = set()
        self._n_blocks = 0

    @property
    def effective_window_blocks(self) -> float:
        """指数核的等效窗口长度：sum(k>=0, 2^(-k/hl))。"""
        per_block_decay = 0.5 ** (1.0 / self.half_life)
        return 1.0 / (1.0 - per_block_decay)

    def observe(self, block: int, dst_shard: int, value: int) -> None:
        if value <= 0:
            return
        block = int(block)
        self._seen_blocks.add(block)
        self._n_blocks = len(self._seen_blocks)
        if self._first_block is None or block < self._first_block:
            self._first_block = block

        late_decay = 1.0
        if self._last_block is not None and block > self._last_block:
            gap = block - self._last_block
            decay = 0.5 ** (gap / self.half_life)      # 指数衰减
            for s in self._rate:
                self._rate[s] *= decay
            self._last_block = block
        elif self._last_block is None:
            self._last_block = block
        elif block < self._last_block:
            # 迟到的旧事件先折算到当前锚点；锚点本身绝不能倒退。
            late_decay = 0.5 ** ((self._last_block - block) / self.half_life)
        self._rate[dst_shard] = (self._rate.get(dst_shard, 0.0)
                                 + float(value) * late_decay)

    def ready(self, block: int, offset_blocks: int = 0) -> bool:
        # 既要求足够多的不同观测块，也让 deterministic offset 真正生效。
        if self._first_block is None or self._n_blocks < self.min_blocks:
            return False
        return int(block) >= (self._first_block + self.min_blocks - 1
                              + max(0, int(offset_blocks)))

    def demand(self, block: int, shard_count: int) -> t.Tuple[t.List[int], int]:
        # 对每个分片做一次块衰减。_rate 本身就是指数窗口内的累计需求，
        # 不再额外乘 half_life；额外相乘会让 base-stock 的量纲多出一维。
        decay = 1.0
        if self._last_block is not None and block > self._last_block:
            gap = block - self._last_block
            decay = 0.5 ** (gap / self.half_life)
        lam = [0] * shard_count
        total = 0
        for s in range(shard_count):
            r = self._rate.get(s, 0.0) * decay
            v = r
            lam[s] = int(v)
            total += int(v)
        return lam, total

    def count(self, block: int) -> int:
        return self._n_blocks


# ---------------------------------------------------------------------------
# 目标分配（allocation.go computeTargetAllocationForShards + applyConstraints）
# ---------------------------------------------------------------------------

def compute_target(actual: t.Sequence[int], demand: t.Sequence[int],
                   q_min: float = 0.0, cap: float = 0.0,
                   eligible: t.Optional[t.Sequence[int]] = None) -> t.List[int]:
    """τ 向量：只重排 eligible 子账户的资金池（封闭池语义，Go 同）。
    非 eligible 的 τ_i = β_i 原样保留——A/B 实验不许偷偷从别的分片进钱。"""
    target = [int(b) for b in actual]
    n_shards = len(actual)
    valid = list(eligible) if eligible is not None else list(range(n_shards))
    seen: t.Set[int] = set()
    valid = [i for i in valid if 0 <= i < n_shards and not (i in seen or seen.add(i))]
    if not valid:
        return target
    total_balance = sum(actual[i] for i in valid)
    total_demand = sum(demand[i] for i in valid if i < len(demand))
    if total_demand == 0:
        return target                       # 零需求保持现状（Go 同）

    # —— Phase A：比例整数分配（论文 Eq.3），余数给最大 λ 的 shard ——
    max_idx = valid[0]
    allocated = 0
    for i in valid:
        d = demand[i] if i < len(demand) else 0
        target[i] = total_balance * d // total_demand
        allocated += target[i]
        di = demand[i] if i < len(demand) else 0
        dmax = demand[max_idx] if max_idx < len(demand) else 0
        if di > dmax:                       # 严格大于 ⇒ 平局保留先出现者（=最小分片号）
            max_idx = i
    if allocated < total_balance:
        target[max_idx] += total_balance - allocated

    # —— Phase B：q_min 地板 / 集中度上限 + reconcile（Go applyConstraints）——
    if len(valid) > 1:
        _apply_constraints(target, total_balance, valid, q_min, cap)
    return target


def _apply_constraints(target: t.List[int], total_balance: int, valid: t.List[int],
                       min_ratio: float, max_ratio: float) -> None:
    n = len(valid)
    # 地板：每 eligible shard ≥ ρ·min_ratio/n（BPS 截断，与 Go 一致）
    if min_ratio > 0:
        floor_bps = int(min_ratio * BPS_SCALE)
        floor = total_balance * floor_bps // (n * BPS_SCALE)
        if floor > 0:
            deficit = 0
            above: t.List[int] = []
            above_excess = 0
            for i in valid:
                if target[i] < floor:
                    deficit += floor - target[i]
                    target[i] = floor
                else:
                    excess = target[i] - floor
                    if excess > 0:
                        above.append(i)
                        above_excess += excess
            if deficit > 0 and above_excess > 0:
                remaining = deficit
                for idx, i in enumerate(above):
                    excess = target[i] - floor
                    share = deficit * excess // above_excess
                    share = min(share, excess)
                    if idx == len(above) - 1:
                        share = remaining           # 末位吸收残差
                    share = min(share, remaining)
                    target[i] -= share
                    remaining -= share
    # 上限：任何 shard ≤ ρ·max_ratio；溢出按 gap 比例回摊（cap 与地板冲突时 cap 优先）
    if 0 < max_ratio < 1.0:
        cap_bps = int(max_ratio * BPS_SCALE)
        cap = total_balance * cap_bps // BPS_SCALE
        if cap > 0:
            overflow = 0
            for i in valid:
                if target[i] > cap:
                    overflow += target[i] - cap
                    target[i] = cap
            if overflow > 0:
                gaps = [(i, cap - target[i]) for i in valid]
                gap_sum = sum(g for _, g in gaps)
                if gap_sum > 0:
                    remaining = overflow
                    for idx, (i, g) in enumerate(gaps):
                        share = overflow * g // gap_sum
                        share = min(share, g)
                        if idx == len(gaps) - 1:
                            share = remaining
                        share = min(share, remaining)
                        target[i] += share
                        remaining -= share
    # reconcile：Στ 与 ρ 的差额归最大 τ（Go 同）
    allocated = sum(target[i] for i in valid)
    best = max(valid, key=lambda i: target[i])
    diff = total_balance - allocated
    if diff != 0:
        target[best] += diff


# ---------------------------------------------------------------------------
# 触发与转移规划（reallocation.go computeOptimalTransfers）
# ---------------------------------------------------------------------------

Transfer = t.Tuple[int, int, int]          # (from_shard, to_shard, amount_wei)


def compute_transfers(actual: t.Sequence[int], target: t.Sequence[int],
                      epsilon: float, trigger_mode: str = "excess_only"
                      ) -> t.List[Transfer]:
    """死区只决定要不要动；一旦触发，T 覆盖【全部】正负 gap（Go phase3 注释原文），
    执行完每个子账户精确回到 τ。返回 [] 表示未触发。

    trigger_mode：excess_only（论文/Go：只有超配触发，短缺方被动）
                  two_sided（手稿：|β−τ| 任一方向超死区即触发）——配置项。"""
    triggered = False
    for i, bal in enumerate(actual):
        tgt = target[i] if i < len(target) else 0
        margin = percent_of(tgt, epsilon)
        if trigger_mode == "two_sided":
            if bal > tgt + margin or bal < tgt - margin:
                triggered = True
                break
        else:                                   # excess_only（默认，随 Go）
            if bal > tgt + margin:
                triggered = True
                break
    if not triggered:
        return []

    surplus, deficit = [], []
    for i, bal in enumerate(actual):
        tgt = target[i] if i < len(target) else 0
        gap = bal - tgt
        if gap > 0:
            surplus.append([i, gap])
        elif gap < 0:
            deficit.append([i, -gap])
    # 论文 Algorithm 1：最大盈余先配最大短缺；stable 排序，键取负金额保降序+平局按分片号
    surplus.sort(key=lambda s: (-s[1], s[0]))
    deficit.sort(key=lambda d: (-d[1], d[0]))

    plans: t.List[Transfer] = []
    si = di = 0
    while si < len(surplus) and di < len(deficit):
        amt = min(surplus[si][1], deficit[di][1])
        if amt <= 0:
            if surplus[si][1] <= 0:
                si += 1
            if deficit[di][1] <= 0:
                di += 1
            continue
        plans.append((surplus[si][0], deficit[di][0], amt))
        surplus[si][1] -= amt
        deficit[di][1] -= amt
        if surplus[si][1] == 0:
            si += 1
        if deficit[di][1] == 0:
            di += 1
    return plans


def valve_plans(actual: t.Sequence[int], init_wei: int, cap_mult: float = 2.0,
                ) -> t.List[Transfer]:
    """傻瓜水位阀（消融策略，无需求预测，区别于 compute_transfers 的 λ 比例分配）。

    触发：存在某分片余额 ≥ init×cap_mult（默认 2 倍初始）才算"水满"。
    一旦触发：把最满分片超出 init 的部分，补给所有【低于 init】的分片，
    缺口最大者优先（水往低处流）。只看余额高低，不看需求往哪去。

    返回 [] = 未满/无可补（低于 init 的分片已无缺口）。返回的转移直接就是
    (src=最满者, dst=低处, amt)。这是审计 F3 的对照臂：同样走 burn+铸两段，
    但"往哪搬"不含任何路由需求信息。"""
    if init_wei <= 0 or not actual:
        return []
    cap = int(init_wei * cap_mult)
    if cap <= 0:
        return []
    if max(actual) < cap:
        return []
    r = max(range(len(actual)), key=lambda i: actual[i])      # 最满者
    surplus = actual[r] - init_wei
    if surplus <= 0:
        return []
    needy = [i for i in range(len(actual))
             if i != r and actual[i] < init_wei]
    needy.sort(key=lambda i: actual[i])                        # 缺口最大优先
    plans: t.List[Transfer] = []
    rem = surplus
    for i in needy:
        give = min(rem, init_wei - actual[i])
        if give <= 0:
            continue
        plans.append((r, i, give))
        rem -= give
        if rem <= 0:
            break
    return plans


def base_target(actual: t.Sequence[int], demand: t.Sequence[int],
                window_blocks: int, lead_blocks: float, safety: float,
                floor_frac: float = 0.0) -> t.List[int]:
    """base-stock 缓冲线（需求侧缺货触发的特异性对照策略）。

    思想：每分片只在自己"近期流出速率"对应的缓冲线下补钱，而不是像
    valve 那样所有分片补到同一个 init。rate_d = demand_d / window_blocks
    （ETH/块，近 W 块流往 d 的平均流出）；缓冲线 S_d = rate_d × lead × safety，
    地板 S_d ≥ floor_frac × ρ（防某分片被完全放空后突然来一笔）。

    注意 ΣS_d 不一定等于 Σβ：钱多就闲置，别乱搬（这正是 valve 省搬量的关键），
    只有当某分片低于它自己的线才触发。返回目标向量，配合 compute_transfers 用。"""
    n = len(actual)
    rho = sum(actual)
    floor = int(rho * floor_frac / n) if floor_frac > 0 else 0
    out: t.List[int] = []
    for i in range(n):
        d = demand[i] if i < len(demand) else 0
        rate = d / max(window_blocks, 1)              # ETH/块（浮点）
        s = int(rate * lead_blocks * safety)
        out.append(max(floor, s))
    return out


def topup_plans(actual: t.Sequence[int], target: t.Sequence[int],
                epsilon: float,
                surplus_epsilon: t.Optional[float] = None) -> t.List[Transfer]:
    """只补缺口、绝不下抽的再平衡（valve 纪律 × 需求份额目标）。

    与 compute_transfers 的全部差别：compute_transfers 触发后把【所有】盈余
    抽到恰为 τ（全量重排，proportional 因此每次事件搬一大坨）。本函数只
    把低于 τ 的分片补到 τ，抽取方只出"高于自己 τ 的那部分"，补完即停——
    高于 τ 的闲置资金不被动。缺货侧触发（某分片 < τ−ετ 才动）。

    返回 [] = 无缺口/无盈余可补。仅填缺口、可部分填（盈余不够就欠着）。"""
    source_eps = epsilon if surplus_epsilon is None else float(surplus_epsilon)
    deficit = [[i, target[i] - actual[i]]
               for i in range(len(actual))
               if target[i] - actual[i] > 0
               and actual[i] < target[i] - percent_of(target[i], epsilon)]
    surplus = [[i, actual[i] - target[i]]
               for i in range(len(actual))
               if actual[i] - target[i] > 0
               and actual[i] > target[i] + percent_of(target[i], source_eps)]
    if not deficit or not surplus:
        return []
    deficit.sort(key=lambda x: -x[1])          # 缺口最大先补
    surplus.sort(key=lambda x: -x[1])          # 盈余最大先抽
    plans: t.List[Transfer] = []
    si = di = 0
    while si < len(surplus) and di < len(deficit):
        amt = min(surplus[si][1], deficit[di][1])
        if amt <= 0:
            si += 1
            continue
        plans.append((surplus[si][0], deficit[di][0], amt))
        surplus[si][1] -= amt
        deficit[di][1] -= amt
        if deficit[di][1] == 0:
            di += 1
        if surplus[si][1] == 0:
            si += 1
    return plans


def compute_deviation(actual: t.Sequence[int], target: t.Sequence[int]) -> int:
    """L1 距离 Σ|β−τ|（Go ComputeDeviation），报告用。"""
    return sum(abs(b - (target[i] if i < len(target) else 0))
               for i, b in enumerate(actual))
