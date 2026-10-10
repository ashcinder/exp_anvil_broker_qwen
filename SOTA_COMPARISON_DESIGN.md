# TDR 对比实验设计：Pareto 图 + 盈亏平衡线 + SOTA 算法实现说明

**版本**：2026-10-10 第一版
**依据**：`tmp/lit/` 下 26 篇原文（逐篇读过算法）+ 本组已有实测数据（`submit_www/EXPERIMENT_REPORT_CN.md` §8）
**目的**：把 TDR 的"**降 relay 且不造成过多额外 CTX**"这一双重卖点，做成一张可归因、可引用、可反驳的对照图。

---

## 0. 一句话

> 横轴 = relay CTX 数（越低越好），纵轴 = 再平衡搬运次数（越低越好）。
> 画一条**过 plain 点的盈亏平衡射线**，线上是亏损区。
> **TDR 独占左下角，且经济阈值比全量重排低 18.7 倍。**

---

## 1. 图的设计

### 1.1 坐标轴

| 轴 | 量 | 方向 | 数据来源 |
|---|---|---|---|
| x | relay CTX 数 | 越低越好 | 已有，`report.json → arms[].relay` |
| y | **再平衡搬运次数** | 越低越好 | 已有，`arms[].transfers` |
| 辅助 | 搬量 ETH / TDR 段占比 | — | 已有，作稳健性副轴 |

**为什么主纵轴选"搬运次数"而不是"搬量"**：在本平台里，一笔搬运 = 两段链上交易（burn + mint），成本以**每笔交易的固定 gas** 为主；我们没有外部的按比例收费（burn/mint 是内部原语，不存在 swap fee）。所以**固定成本 ∝ 搬运次数**是更忠实的模型。搬量作副轴，用于检验结论不依赖成本模型的选择。

### 1.2 盈亏平衡线 —— 把论文 Eq.(11) 画出来

论文 §V-D 已有：

$$\kappa\,\Delta n_b(H)\ \ge\ \mu\,n_r(H) \tag{Eq.11}$$

- $\kappa$ = broker 每多服务一笔 CTX 相对 relay 的净收益
- $\mu$ = 每笔再平衡搬运的费用
- $\Delta n_b(H)$ = 增量服务的 CTX 数
- $n_r(H)$ = 再平衡搬运次数

代入图上坐标：参考点取 plain，记为 $(x_0, 0)$，$x_0 = 24{,}848$；则 $\Delta n_b = x_0 - x$，$n_r = y$。

$$\boxed{\ \kappa\,(x_0-x)\ \ge\ \mu\,y\ \iff\ y\ \le\ \frac{\kappa}{\mu}\,(x_0-x)\ }$$

**这就是一条过 $(x_0,0)$、斜率为 $-\kappa/\mu$ 的射线：射线下方（含线上）＝盈利，上方＝亏损。**

把每一点折算成一个无量纲数，定义为**经济阈值**：

$$\rho^\* = \frac{y}{x_0-x}\quad\text{（该方案所需的最小收益/成本比 } \kappa/\mu\text{）}$$

$\rho^\*$ 越小 = 对经济环境越宽容 = 越稳健。**这个数可以直接从已有实验数据算出来**（见 §1.3）。

### 1.3 用已有实测数据算出的阈值

数据取自 `EXPERIMENT_REPORT_CN.md` §8（16 分片 × 50 broker × 40,000 CTX）：

| 方案 | relay CTX | $\Delta n_b$ | 搬运次数 | **$\rho^\*$（所需 $\kappa/\mu$）** | 搬量 ETH | 搬量/Δn_b |
|---|---:|---:|---:|---:|---:|---:|
| plain（不再平衡） | 24,848 | 0 | 0 | — | 0 | — |
| valve（静态水位阀） | 20,863 | 3,985 | 273 | **0.069** | 13,045 | 3.27 |
| proportional（ε=0 全量重排） | 110 | 24,738 | 22,642 | **0.915** | 813,408 | 32.9 |
| topup 硬窗（ε=0.1） | 72 | 24,776 | 17,206 | **0.694** | 807,759 | 32.6 |
| **TDR 定稿** | **20** | **24,828** | **1,227** | **0.049** | **163,674** | **6.59** |

**三条立刻可用的结论：**

1. **TDR 的经济阈值最低（0.049）** —— 只要"多服务一笔 CTX 的收益"达到"一笔搬运成本的 5%"，TDR 就盈利。
2. **全量重排需要 0.915，是 TDR 的 18.7 倍** —— 收益几乎要等于一整笔搬运成本才不亏。这就是"降了 relay 但搬运爆炸"的代价。
3. **valve 的阈值（0.069）反而比 TDR 高** —— 虽然它只搬 273 次（搬运量最少），但它几乎没减少 relay（24,848→20,863，只降 16%），$\Delta n_b$ 太小，**经济上比 TDR 更差**。这一条特别有说服力：**省搬运不等于省钱。**

> 把三条合起来讲：**valve 败在"服务不起"，proportional 败在"搬不起"，TDR 是唯一两头都成立的。**

### 1.4 图示

```
y = 再平衡搬运次数（对数轴）
  10^4 ┤                                    ● proportional (22,642@110)
       │                                    ● topup 硬窗 (17,206@72)
       │            ╱ 亏 损 区
       │          ╱        ● loopmax ★预测（relay 好但搬满）
  10^3 ┤        ╱      ● mcflow(≈proportional)
       │      ╱    ● spider-vol ★预测
       │    ╱   ● pickhardt ★预测
       │  ╱  ● flexichain ★预测（同模型，只差目标）
  10^2 ┤╱  ● autoloop ★预测
       │  ● ucrb ★预测
       │──────────────────────────── ← 盈亏平衡射线 y=(κ/μ)(x₀−x)
       │ ● valve (273@20,863)     ▲ TDR (1,227@20)
  10^1 ┤                  ★ TDR 位于射线下方很远
       │
       ● plain (0@24,848)   ● pnps-static ★预测（几乎不搬，但 relay 不降）
       └────────────────────────────────────────────────→ x = relay CTX 数
       24,848                                        20
         （差）                                    （好）
```

- **射线斜率不是固定的** —— 它是 $\kappa/\mu$ 的函数。建议**画一簇射线**（例如 $\kappa/\mu \in \{0.05, 0.1, 0.25, 0.5, 1.0\}$），让读者自己看在哪一档经济环境下哪些方案开始亏损。
- **TDR 与 proportional 之间要标注"18.7×"** —— 这是全图最有冲击力的一个数。
- 射线**必须过 plain 点**，因为"不再平衡"是 broker 的默认选择，盈亏都是相对它而言的。

### 1.5 严谨性声明（必须写进论文）

Eq.(11) 目前**仍是规格说明，不是实测** —— 因为部署尚未把费用 $\mu$ 接入再平衡路径（论文 §V-D 与 §VI 已如实声明）。因此图上这条线应表述为：

> "在什么经济条件下该方案盈亏平衡"，而**不是**"该方案实测盈利/亏损"。

这不是弱点。把 $\rho^\*$ 作为**方案的内禀属性**报出来，反而比假设一个具体的 $\mu$ 更稳健 —— 因为我们不依赖任何关于 gas 价格与手续费率的假设。

---

## 2. SOTA 方案的算法设计

每条给出：出处 → 系统模型 → **原始算法** → 触发 → 目标 → 搬运 → **如何实例化到本设定** → **丢掉了什么**。

> 共同的移植原则：所有方案的**多方协调机制**（环分解、MPC、HTLC、领导者选举、Pedersen 承诺）在"单一运营者拥有全部账户"下都无对象，**一律丢弃**；只保留其**决策规则**。这一句必须写进论文，否则会被读成"苹果比橘子"。

---

### 2.1 `flexichain` ★★★ 首选对照

**出处**：Mohanty et al., *Flexichain: Flexible Payment Channel Network to Defend Against Channel Exhaustion Attack*, **ACM TOPS 27(4), Article 30, 2024**（DOI 10.1145/3687476，CC-BY）
**文件**：`2024_TOPS_Flexichain-flexible-PCN-user-fund-pool.pdf`

**系统模型**：**资金按用户持有，不按通道持有** —— 原文："Rather than keeping funds locked per channel, it **keeps funds per user**"。**这与 TDR 的资金模型完全相同。**

**原始算法**（已核原文 §5）：

```
CoinAllocation(ui, {uj}, ωi, σ'_ij):
    用户 ui 用分配策略 ω_i / |{u_j}| 给每条相邻通道分配抵押
    前提条件包含："there is no coin allocated with the channel"  ← 只能分配一次

CoinShift(νi, uk, uj):
    前提：(ϒ_ki − ν_i) ≥ 0
    效果：ϒ_ik ← ϒ_ik − ν_i ;  ϒ_ij ← ϒ_ij + ν_i
    注意：ν_i 是【传入参数】，算法本身不计算该搬多少
```

**触发**：支付时被动 —— 某条通道余额不足以支付当前金额时才 CoinShift。
**目标**：**静态均分 `ω_i / |{u_j}|`**，一次设定，不可重分配。
**搬运**：金额由外部给定，只负责执行。

**实例化到本设定**：
- 目标向量 $\tau_s = \rho / |\mathbb{S}|$（对全部 |S| 个分片均分）—— 直接对应 `ω_i/|{u_j}|`
- 触发：某分片余额不足以服务当前 CTX 时，从余额最高的分片移位
- 搬运：补足缺口

**丢掉什么**：BPTLC 原子性、UC 框架证明、隐私保护 —— 单运营者无需对手方共识。

**为什么它是最佳对照**：**模型相同、动作空间相同，唯一的差别是目标策略**（它均分，TDR 按需求比例）。因此两者之间的 relay 差距 **可以完全归因于"需求驱动的目标分配"** —— 这正是 R2 点名要隔离的东西。

**预测**：落 **A 型（目标不看需求）** → relay 应接近 valve 档（约 20,000±），因均分线在方向性流量下不足以喂饱热点目的分片。

---

### 2.2 `loopmax` ★★ 隔离"搬运幅度控制"

**出处**：*Deep Reinforcement Learning-based Rebalancing Policies for Profit Maximization of Relay Nodes in PCNs*, **IACR ePrint 2022/1385**
**文件**：`2022_ePrint_Autoloop-Loopmax-RebEL-when-and-how-much-rebalancing.pdf`

**系统模型**：**单一中继节点**在自己的通道间调度 —— 全文献里与 TDR 的决策问题最同构的一篇。

**原始算法**（原文 Alg.2，参数含默认值）：

```
每 T_check，对每个邻居 n：
    Â_net_Nn = 经验净需求速率
    if Â_net_Nn < 0:                      # 预测会被抽空
        ETTD = b_Nn / |Â_net_Nn|          # 预计耗尽时间
        if ETTD < T_check + T_conf:
            swap-in = max{ φ⁻¹(B_N), b_nN }   ← 【搬满，取最大可能值】
    elif Â_net_Nn > 0:                    # 预测会堆积
        ETTS = b_nN / Â_net_Nn
        if ETTS < T_check + T_conf:
            swap-out = b_nN               ← 【搬满】
    另可叠加安全边际：留 2 分钟估计流量
```

**参数**：$T_{check} = T_{conf} = 10$ 分钟；安全边际 = 2 分钟流量。

**触发**：**预测型** —— 预计在一个"决策+确认"horizon 内耗尽/饱和。
**目标**：无显式目标向量；等价于"维持在中位"。
**搬运**：**搬满（max possible）** —— 这是关键。

**实例化**：用我们已有的 EWMA 估计器算 $\widehat{Â}_{net}$，按上式触发，搬运量取"目标中点所需的最大值"。

**丢掉什么**：与外部 LSP 的 submarine swap —— 换成内部 burn/mint 转账，成本模型从"swap fee F + miner fee M"变成"每笔转账的 gas"。

**为什么选它**：**它的预测和我们一样准，但它搬满、我们没有**。这一条专门隔离**"看得准不够，还得忍得住"** —— 即死区/幅度控制的价值。

**预测**：落 **B 型（无克制）** → **relay 可能比 TDR 更低**（前瞻 + 搬满），但**搬运次数会高出约一个数量级**。

**⚠️ 风险**：这是唯一可能在某单项上击败 TDR 的臂。见 §4。

---

### 2.3 `spider-vol` ★ 双坑同踩

**出处**：Sivaraman et al., *High Throughput Cryptocurrency Routing in Payment Channel Networks*, **USENIX NSDI 2020**, §7.4
**文件**：`2020_NSDI_Spider-multipath-routing-PCN.pdf`

**原始算法**（原文 §7.4 的再平衡规则）：

> "every router periodically reallocates funds between its payment channels to **equalize their available balance**. The frequency of rebalancing for a router is defined by **the number of successful transaction-units (in e) between consecutive rebalancing events**."

**参数**：**每流过 10,000e 均衡一次**（对比 LND 每 10e 一次）。盈亏平衡：每 10,000e 流水对应约 1e 链上成本。

**触发**：**按成交量** —— 与失衡程度无关，流量到了就搬。
**目标**：**均分**（equalize available balance）。
**搬运**：waterfilling —— 送往最不缺的一方。

**实例化**：计数器累计每个分片流过的金额，每 10,000 ETH 触发一次均衡，目标为均分。

**丢掉什么**：多路径路由与拥塞控制（那是路由不是再平衡）。

**预测**：**A+B 型双坑** —— 均分目标喂不饱热点分片（relay 高），按流水定频又与真实失衡无关（搬运浪费）。**最容易打的靶子。**

---

### 2.4 `pnps-static` ★ 在线 vs 静态

**出处**：Li, Miyazaki, Zhou, *Secure Balance Planning of Off-blockchain Payment Channel Networks*, **IEEE INFOCOM 2020**（DOI 10.1109/INFOCOM41043.2020.9155375）
**文件**：`2020_INFOCOM_PnP-initial-balance-planning.pdf`

**系统模型**：决定**建链时**该往每条通道存多少初始余额 —— 一次性的规划问题。

**原始算法**：**chance-constrained（机会约束）余额规划** —— 在满足需求"以高概率"的约束下决定初始分布。原文摘要："decide how many funds should be deposited into payment channels as initial balances… seldom studied by existing work"。

**触发**：**无** —— 只在建链时执行一次，之后不再调整。
**目标**：一次性的初始分布向量。
**搬运**：**没有后续搬运**（这正是它的特征）。

**实例化**：用真实 λ 分布做一次离线最优分配作为初始余额，全程不再平衡。

**为什么选它**：它能证明**"光规划不够，必须在线持续再平衡"** —— 因为方向性流量是持续漂移的，任何一个静态最优解都会随时间退化。

**预测**：落 **A 型极端** → 搬运次数 ≈ 0，但 **relay 会随运行渐近 plain**（起手好，后期退化）。它的轨迹形状本身就是论据。

---

### 2.5 其余可选臂（按需加）

| 臂 | 出处 | 触发 | 目标 | 搬运 | 预测落点 |
|---|---|---|---|---|---|
| `autoloop` | ePrint 2022/1385 | 余额穿 `low=0.3·C` / `high=0.7·C` | midpoint `C(low+high)/2` | 贪心配对 | **A 型**：relay 中高 |
| `pickhardt` | arXiv 1912.09555 | 候选集 `ζ−ν>0`（极易满足） | 公平份额 `ν_u=τ_u/κ_u` | 金额 `c·(ζ−ν)`，追平均值 | **A+B 型** |
| `ucrb` | IACR ePrint 2025/1023 | **支付失败后**（反应式） | 无 | 选源使 `Σ移位 == 缺口`，优先余额高者 | **滞后型**：搬运低但 relay 降不够 |
| `overpass-qp` | IACR ePrint 2024/1526 | `balance < threshold×capacity` | **θ 为自由输入**（θ=1/n 均分；θ∝λ 即需求比例） | 凸 QP：`min Σ w_i(B_i+ΔB_i−θ_i·L)²` s.t. `ΣΔB=0` 与上下界，**KKT 闭式解** | θ=均分 → **A+B**；θ=需求 → **B 型** |
| `mcflow` | FC 2022 (Hide & Seek) | — | 任意 | circulation min-cost flow | 单运营者下退化为全量重排 = `proportional` |

**`overpass-qp` 的建议用法**：跑 **θ=均分** 与 **θ=需求** 两个变体。前者证明"目标不看需求的代价"，后者证明"即使目标对了，没有死区仍会搬运爆炸"。**两个变体合起来正好把 TDR 的两项贡献各自隔离出来。**

---

## 3. 预测落点汇总（跑之前存档）

| 方案 | 目标看需求？ | 有克制？ | 预测 relay | 预测搬运 | 预期落点 |
|---|---|---|---|---|---|
| plain | — | — | 最高 | 0 | 右下角 |
| pnps-static | ✗ 静态 | 完全不搬 | 高（渐近 plain） | ≈0 | 右下角 |
| **flexichain** | **✗ 静态均分** | 有 | **高** | 中 | 右下偏中 |
| autoloop | ✗ midpoint | 有带 | 中高 | 中 | 中部 |
| spider-vol | ✗ 均分 | ✗ | 高 | 高 | 右上 |
| pickhardt | ✗ 公平份额 | ✗ | 高 | 高 | 右上 |
| overpass-qp(θ=均分) | ✗ | ✗ | 高 | 高 | 右上 |
| ucrb | ✓（补缺口） | ✓ | 中 | 低 | 中部偏左 |
| **loopmax** | ✓（预测） | **✗ 搬满** | **可能最低** | **最高** | **左上** |
| overpass-qp(θ=需求) | ✓ | ✗ | 低 | 高 | 左上 |
| mcflow（≡proportional） | ✓ | ✗ | 低（110） | 最高（22,642，**已实测**） | 左上 |
| **TDR** | ✓ 需求比例 | ✓ 死区 | **20（已实测）** | **1,227（已实测）** | **左下角** |

**图上的叙事**：所有方案要么落在**右侧**（relay 降不下来），要么落在**上方**（搬运爆炸），**只有 TDR 在左下角**。

---

## 4. 风险与应对

| 风险 | 说明 | 应对 |
|---|---|---|
| **`loopmax` 可能 relay 更低** | 它前瞻一个 horizon 并搬满 | **不主张"relay 最低"，主张"Pareto 最优"**。它用约一个数量级的搬运换取那点 relay 差距。在图上是清晰的左上点，代价一目了然 |
| **`overpass-qp`(θ=需求) 可能 relay 接近 TDR** | 目标策略实质相同 | 它没有死区 → 搬运必然接近全量重排。**同时报两个 θ 变体**，把"目标贡献"与"死区贡献"拆开 |
| **`flexichain` 若 relay 不差** | 说明均分在 16 分片下够用 | 那就要看方向集中度：我们的优势区间是"多分片 + 目的高度集中"（`REPORT §10`）。**应补一组低集中度对照**，明确写出优势边界 —— 这是诚实且已在前文声明过的 scoping |
| **盈亏平衡线被质疑"μ 未接入"** | Eq.(11) 确未实测 | 报 $\rho^\*$ 而非绝对盈亏（§1.5）。$\rho^\*$ 与任何 gas/费率假设无关 |

---

## 5. 待办

- [x] 实现 4 条核心臂：`flexichain` / `loopmax` / `spider-vol` / `pnps-static`（exp033，2026-10-10）
- [x] 实现辅助臂：`autoloop` / `ucrb` / `overpass-qp`（θ=均分 与 θ=需求 两个变体）
      —— 未做 `pickhardt`（公平份额需通道对手方语义）；`mcflow` 单运营者下退化为
      全量重排，已由 exp008 的 `proportional` 臂代表，不重复跑
- [ ] 跑 5 场 × 全部臂，出中位数 + 区间（**当前只跑了 1 场分析档**，见 §6）
- [x] 出图：主图（relay × transfers + 盈亏射线簇），见
      `experiments/exp033_sota_comparison/out/MERGED_pass1_2/figs/exp033_pareto.png`
- [ ] 副图（relay × 搬量）
- [ ] 补齐 `u`：把费用接入再平衡路径，把 $\rho^\*$ 换成实测盈亏（当前为未来工作）
- [x] 文献入库 26 篇，见 `tmp/lit/`
- [x] 三轴算法提取，见 `ALGORITHM_MATRIX.md`

---

## 6. 首轮实测（exp033，2026-10-10）

**状态**：**分析档 1 场**，`formal=false`，**不得作为论文数据**；正式档为 3 场。

**负载**：与 exp025 逐参数相同（16 分片 × 50 broker × 40,000 CTX、
route_seed 7、regime-shift 轮转热点 11/3、每 broker 每分片 45 ETH）。
本负载比 §1.3 引用的 §8 档更狠（69.2% 的 CTX 被改写目的分片），
所以 plain 的 relay 是 30,404（占 76%）而非 §8 的 24,848；
**绝对数不可与 §1.3 的表混用，但同一场内的横向比较是干净的。**

| 方案 | relay CTX | 搬运次数 | 搬量 ETH | **$\rho^\*$** |
|---|---:|---:|---:|---:|
| plain | 30,404 | 0 | 0 | — |
| **TDR（本方案）** | **13** | **3,262** | 115,452 | **0.107** |
| autoloop | 2,268 | 5,218 | 79,869 | 0.185 |
| ucrb | 11,327 | 3,650 | 41,077 | 0.191 |
| loopmax | 29 | 6,315 | 147,551 | 0.208 |
| spider-vol | 10,051 | 8,566 | 57,966 | 0.421 |
| pnps-static | 28,645 | 750 | 26,418 | 0.426 |
| overpass-qp (θ∝λ) | 78 | 27,691 | 235,027 | 0.913 |
| flexichain | 2,876 | 25,231 | 88,116 | 0.917 |
| overpass-qp (θ=1/n) | 1,860 | 31,117 | 93,078 | 1.090 |

全部 10 臂 G1–G4 + burn≡mint 全 True。

**四条可直接写进论文的结论**：

1. **TDR 独占左下角，且是唯一 ρ\* < 0.11 的方案**。
   在把 relay 压到 5,000 以下的全部方案里，TDR 在 relay 与搬运次数两个维度上
   **同时最低**（严格支配）。
2. **两个隔离实验各自成立**（这是 §4 风险应对想要的效果）：
   - `overpass-qp` 两个 θ 变体只有一个变量之差：
     θ=1/n → relay 1,860；θ∝λ → relay **78**（24 倍差），而搬运次数几乎相同
     （31,117 vs 27,691）。**"目标不看需求"要付 24 倍 relay 的代价，且省不下搬运。**
   - `overpass-qp(θ∝λ)` 与 TDR 的目标规则**实质相同**，唯一差别是**死区**：
     搬 27,691 次 vs 3,262 次（**8.5 倍**），换来的 relay 差是 78 vs 13
     —— 而两者都已在 0.2% 以下。**死区贡献的是搬运次数，不是 relay。**
3. **loopmax 是唯一在 relay 上接近 TDR 的臂**（29 vs 13），代价是 1.9 倍搬运、
   ρ\* 翻倍（0.208）。这正对应 §2.2 的"看得准不够，还得忍得住"。
4. **两条"不看需求"的臂给出同一教训，路径不同**：
   flexichain（静态均分）relay 只降到 2,876 却搬了 25,231 次（ρ\*=0.917）；
   spider-vol（按流水定频）relay 只降到 10,051 还搬 8,566 次（ρ\*=0.421）。
   pnps-static 最极端：搬 750 次，但 relay 几乎没降（28,645），
   证明**光靠离线规划不够，方向性流量会持续漂移**。

> 交给写作 agent 的独立材料（含可写/不可写清单、口径换算、出处）：
> `EXP033_SOTA_FINDINGS_CN.md`。改数字时两处要同步改。

**一处工程发现**：本轮首次触发了 `lost_moves` 的台账 schema bug——
`brokerlab/tdr_engine.py` 记的是 3 元组 `(src,dst,amt)`，而
`pipeline.normalize_tdr_moves` 与 `submit_regime_shift/scripts/run.py`
都要求 4 元组带 burn 块高（判失的 burn 已上链，对账必须按它定位）。
此前所有正式臂都没丢过搬运，所以从未暴露。已修：`fail()` 必须收到
引擎实测的 `burn_block`，否则拒绝落账（宁可不记，也不编块高）。
**注意**：`experiments/exp029_added_parameter_sensitivity/` 里有一份
同源的 `tdr_engine.py`/`tdr_hook.py` 拷贝，**该 bug 仍在**，下次用 exp029 前要一并修。


**仍未入库 5 篇**（均为闭源或唯一副本被拦，不阻塞）：Cycle (DSN'22)、DRL-PCR (TMC'24)、Broker2Earn (INFOCOM'24)、Broker2Balance (BlockSys'25)、BIND (WWW'26)。前两篇只进 related work。
