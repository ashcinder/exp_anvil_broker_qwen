"""SOTA 再平衡基线策略（纯函数）—— exp033 对照臂的决策规则。

与 tdr_policy.py 的分工：
  tdr_policy.py 逐语义镜像 TDR 论文/Go 参考实现（本组的方案本身，不许改）；
  本文件只放**对照组**的决策规则，每条逐句对应文献里的原始算法，方便审稿人
  拿原文核对"我们按对方的规则跑了，而不是扎了个稻草人"。

统一的移植原则（`SOTA_COMPARISON_DESIGN.md` §2）：
  所有方案的**多方协调机制**（环分解、MPC、HTLC、领导者选举、Pedersen
  承诺）在"单一运营者拥有全部 broker 子账户"下都无对象，一律丢弃；只保留
  其**决策规则**。所以本文件的每个函数只回答三个问题：
    何时动（触发）、动到哪（目标）、动多少（幅度）。

本文件无 I/O、无链上动作；全部金额为 int（wei）。返回 (from, to, amount) 列表。
"""
import typing as t

from .tdr_policy import Transfer, compute_target, compute_transfers, percent_of


# ---------------------------------------------------------------------------
# 公共零件
# ---------------------------------------------------------------------------

def _greedy_pair(deficit: t.Sequence[t.Sequence[int]],
                 surplus: t.Sequence[t.Sequence[int]]) -> t.List[Transfer]:
    """最大缺口配最大盈余的双指针贪心，可部分填（与 tdr_policy 同款配对规则）。

    deficit/surplus 形如 [[shard, amount], …]，原地消耗。这是各文献里
    "greedy pairing / waterfilling" 的共同抽象。"""
    deficits = sorted([list(d) for d in deficit], key=lambda x: (-x[1], x[0]))
    surpluses = sorted([list(s) for s in surplus], key=lambda x: (-x[1], x[0]))
    plans: t.List[Transfer] = []
    si = di = 0
    while si < len(surpluses) and di < len(deficits):
        amt = min(surpluses[si][1], deficits[di][1])
        if amt <= 0:
            if surpluses[si][1] <= 0:
                si += 1
            if deficits[di][1] <= 0:
                di += 1
            continue
        plans.append((int(surpluses[si][0]), int(deficits[di][0]), int(amt)))
        surpluses[si][1] -= amt
        deficits[di][1] -= amt
        if surpluses[si][1] == 0:
            si += 1
        if deficits[di][1] == 0:
            di += 1
    return plans


def uniform_level(actual: t.Sequence[int]) -> int:
    """均分水位 ρ/n（取整）。Flexichain 的 ω_i/|{u_j}| 在闭环资金池下的标量形式。"""
    if not actual:
        return 0
    return sum(int(a) for a in actual) // len(actual)


def uniform_target(actual: t.Sequence[int], q_min: float = 0.0,
                   cap: float = 0.0) -> t.List[int]:
    """均分**向量** τ_i = ρ/n（余数归分片 0），带可选地板/上限。

    复用 TDR 的 compute_target（把需求向量取成常数 [1,…,1] 即得均分），
    这样 q_min/cap/reconcile 的整数化路径与主方案完全同源，对照才干净。"""
    return compute_target(list(actual), [1] * len(actual), q_min=q_min, cap=cap)


def level_plans(actual: t.Sequence[int], level: int) -> t.List[Transfer]:
    """朝一个**标量水位** level 补水：不足者补、超过者出，贪心配对，可部分填。

    与 topup_plans 的差别：目标不是按 λ 算出的份额向量，而是一个统一水位
    （Flexichain 均分后的每片份额、autoloop 的 band 中点）。Σ 不守恒也没关系——
    钱多就闲置、缺口填不满就欠着，这正是这些文献算法的原生行为。"""
    if not actual or level <= 0:
        return []
    deficit = [[i, int(level) - int(a)] for i, a in enumerate(actual)
               if int(level) - int(a) > 0]
    surplus = [[i, int(a) - int(level)] for i, a in enumerate(actual)
               if int(a) - int(level) > 0]
    if not deficit or not surplus:
        return []
    return _greedy_pair(deficit, surplus)


# ---------------------------------------------------------------------------
# 2.1 flexichain（ACM TOPS 27(4) 2024）
# ---------------------------------------------------------------------------

def flexichain_plans(actual: t.Sequence[int]) -> t.List[Transfer]:
    """Flexichain：资金按用户持有（与 TDR 模型相同），目标 = **静态均分**。

    原文 CoinAllocation 把抵押按 ω_i/|{u_j}| 均分到各通道，前提含
    "there is no coin allocated with the channel"（只能分配一次）；
    CoinShift 的搬迁量 ν_i 是**传入参数**，算法本身不计算。因此本设定下
    它等价于：目标水位 = 均分 ρ/n，触发后把低于水位的分片补到水位，
    搬多少由缺口决定（CoinShift 的 ν = 缺口）。

    与 TDR 的唯一差别是**目标不看需求**——这正是要隔离的东西。"""
    return level_plans(actual, uniform_level(actual))


# ---------------------------------------------------------------------------
# 2.2 loopmax（IACR ePrint 2022/1385 Alg.2）
# ---------------------------------------------------------------------------

def loopmax_plans(actual: t.Sequence[int], outflow: t.Sequence[int],
                  inflow: t.Sequence[int], q_min: float,
                  effective_window_blocks: float, horizon_blocks: float,
                  safety_blocks: float) -> t.List[Transfer]:
    """loopmax：**预测型触发 + 搬满**（原文 Alg.2）。

    原文规则：每 T_check 对每个邻居算经验净需求 Â_net；
      Â_net < 0 → ETTD = b/|Â_net|，若 ETTD < T_check + T_conf 则
                  swap-in = max{φ⁻¹(B_N), b_nN}（**取最大可能值**）；
      Â_net > 0 → ETTS = b/Â_net，同理 swap-out。
    本设定把"邻居"换成"本 broker 在各分片的子账户"：
      Â_net(s) = (流入估计 − 流出估计) / 等效窗口长度     （wei/块，有符号）
      流入 = src 分片为 s 的 CTX（Θ1 到账进 broker@src）
      流出 = dst 分片为 s 的 CTX（Θ2 付出）
    触发：存在 s 使 ETTD(s) < horizon（horizon = T_check + T_conf）。
    目标：τ = 需求份额 + safety_blocks 的流出量（"搬满"的安全边际）。
    幅度：ε=0 全量补齐——没有死区，这就是它与 TDR 的关键差别。

    参数全部来自 TdrAgent（EWMA 估计器与 TDR 同一个），见 tdr_engine.py。"""
    n = len(actual)
    if n == 0:
        return []
    window = max(float(effective_window_blocks), 1e-9)
    depleting: t.List[int] = []
    for i in range(n):
        net = (float(inflow[i] if i < len(inflow) else 0)
               - float(outflow[i] if i < len(outflow) else 0)) / window
        if net < 0 and actual[i] / (-net) < float(horizon_blocks):
            depleting.append(i)
    if not depleting:
        return []
    target = compute_target(list(actual), list(outflow), q_min=q_min)
    # 安全边际只抬**预计枯竭**那几片的目标（原文是"给即将不够的一方留流量"）：
    # 若对所有分片同时抬，短缺侧与盈余侧一起抬高，反而互相抵消、搬得更少。
    for i in depleting:
        rate_out = float(outflow[i] if i < len(outflow) else 0) / window
        target[i] += int(rate_out * float(safety_blocks))
    return compute_transfers(actual, target, 0.0, "excess_only")


# ---------------------------------------------------------------------------
# 2.3 spider-vol（USENIX NSDI 2020 §7.4）
# ---------------------------------------------------------------------------

def spider_plans(actual: t.Sequence[int]) -> t.List[Transfer]:
    """Spider §7.4：按**累计流水量**定频，均衡各通道可用余额。

    原文："every router periodically reallocates funds between its payment
    channels to equalize their available balance. The frequency of
    rebalancing is defined by the number of successful transaction-units
    (in e) between consecutive rebalancing events."

    本函数只负责"动到哪"——目标是均分（equalize），幅度是 waterfilling
    全量重排；"何时动"由 TdrAgent 的流水计数器给出（阈值可配）。"""
    target = uniform_target(actual)
    return compute_transfers(actual, target, 0.0, "excess_only")


# ---------------------------------------------------------------------------
# 2.4 pnps-static（IEEE INFOCOM 2020）
# ---------------------------------------------------------------------------

def pnps_target(actual: t.Sequence[int], demand: t.Sequence[int],
                q_min: float = 0.0) -> t.List[int]:
    """PnP：建链时一次性决定初始余额分布（chance-constrained 规划）。

    本设定没有"建链"这个时点，故取等价形式：用第一个完整需求窗口做一次
    离线最优分配（λ 比例），此后**永不再平衡**——这个"此后不动"由
    TdrAgent 的 _pnps_done 冻结位保证。它证明"光规划不够，必须在线持续
    再平衡"，因为方向性流量会持续漂移。"""
    return compute_target(list(actual), list(demand), q_min=q_min)


# ---------------------------------------------------------------------------
# 2.5 辅助臂
# ---------------------------------------------------------------------------

def autoloop_plans(actual: t.Sequence[int], init_wei: int,
                   low: float = 0.3, high: float = 0.7) -> t.List[Transfer]:
    """autoloop（ePrint 2022/1385 的固定阈值变体）：余额穿 band 才动，
    目标 = band 中点 (low+high)/2·C，贪心配对。

    与 loopmax 同源同文献，差别只在触发与幅度：它既不预测、也不搬满，
    而是"穿线才动、动到中点"——正好落在 TDR 与 loopmax 之间。"""
    if init_wei <= 0 or not actual:
        return []
    if not (min(actual) < low * init_wei or max(actual) > high * init_wei):
        return []
    return level_plans(actual, int((low + high) / 2 * init_wei))


def overpass_target(actual: t.Sequence[int], demand: t.Sequence[int],
                    theta: str = "uniform", q_min: float = 0.0) -> t.List[int]:
    """Overpass（ePrint 2024/1526）：凸 QP 的 KKT 闭式解。

    原文目标：min Σ w_i(B_i + ΔB_i − θ_i·L)²  s.t. ΣΔB = 0 与上下界。
    KKT 闭式解就是 ΔB_i = θ_i·L − B_i，即目标向量 τ = θ·L：
      theta="uniform" → θ_i = 1/n（均分目标，**不看需求**）
      theta="demand"  → θ_i ∝ λ_i（需求比例目标）
    原文没有死区（死区来自我们的 ε），所以两个变体合起来正好把
    TDR 的"目标贡献"与"死区贡献"各自隔离出来。"""
    if theta == "demand":
        return compute_target(list(actual), list(demand), q_min=q_min)
    return uniform_target(actual, q_min=q_min)


def ucrb_plans(actual: t.Sequence[int],
               pending: t.Mapping[int, int]) -> t.List[Transfer]:
    """UCRB（IACR ePrint 2025/1023）：**支付失败后**反应式补缺。

    原文：支付失败才动，选源使 Σ移位 == 缺口，优先余额高者。
    没有前瞻、没有目标向量——它是全谱里最"滞后"的一条，用来证明
    "等到失败再补"会留下一段无法服务的窗口。"""
    if not actual:
        return []
    deficit = [[int(s), int(w)] for s, w in pending.items()
               if int(w) > 0 and 0 <= int(s) < len(actual)]
    if not deficit:
        return []
    claimed = {s for s, _ in deficit}
    surplus = [[i, int(a)] for i, a in enumerate(actual)
               if i not in claimed and int(a) > 0]
    if not surplus:
        return []
    return _greedy_pair(deficit, surplus)


__all__ = ["Transfer", "percent_of", "uniform_level", "uniform_target",
           "level_plans", "flexichain_plans", "loopmax_plans", "spider_plans",
           "pnps_target", "autoloop_plans", "overpass_target", "ucrb_plans"]
