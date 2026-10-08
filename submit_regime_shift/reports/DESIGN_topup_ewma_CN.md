# 定稿方案 · topup + EWMA 设计文档

状态：参数定稿（20260909，稳定性 4 重复确认）。
设计史与证据链见同目录 [EXPLORATION_log_CN.md](EXPLORATION_log_CN.md)。

---

## 1 方案定位

这是一套 broker 资金再平衡（TDR）调度方案。

三个硬约束：
- 输入只有链上数据与 broker 自有资金数据。
- 完全由 broker 进程本地执行。
- 不依赖任何外部节点给提示。

目标函数（三个都要低，按优先级）：
- relay 数（终审改判数）。
- TDR 搬运次数。
- TDR 搬钱量。

正式档成绩（16 分片 × 150 垫资 × 40000 笔）：
定稿参数 ε=0.95：relay 5 样本 5/8/15/25/32，中位 15（≤0.08%）。
（3 个来自 exp003 档案 §18/§20，2 个来自 exp008 生产场
 out/20260910_033927，V1-V3 判据全 True。）
曾用参数 ε=0.97：5 样本 16/24/25/48/88，中位 25。
ε 取 0.95 的依据：无一项更差 + 距悬崖 0.05（档案 §18）。
中位差未达统计显著（n=3 对 n=5），按"不劣且更稳"采纳。
搬运 ~1240 次、搬量 16.2-16.4 万 ETH、事件 163-164——几乎恒定。
对照：plain relay 24860（62%）；valve relay 21328（53%）；
旧 proportional relay 93 但搬 77.4 万。
噪声对比：valve 三重复散布 118/148/251（2.5 倍）。
本方案（ε0.97 口径）16-88；ε0.95 三样本 5-25。
深死区一次整补大幅压缩了"补货时机 vs CTX 到达"的相位竞争。
稳定性明细见档案 §16、§17。

## 2 设计的四条教训（为什么长这样）

1. **目标水位必须按需求分配。**
   dst 集中时（热分片吃掉 75% 需求），均匀缓冲失效。
   valve 把钱撒成每片一样，热分片仍被抽穿。
   结论：目标 τ 按需求份额定，热分片多放。
2. **只补缺口，不整池重排。**
   proportional 每次事件把所有分片搬到 τ，搬量是净需求的十几倍。
   结论：盈余分片的闲置钱不动，只给低于目标的分片补。
3. **需求估计必须平滑。**
   20 块硬滑窗有一笔大额滑出就骤降，τ 跟着抖。
   抖动的 τ 导致钱来回倒腾。
   结论：EWMA 指数平滑替代硬窗口。
4. **触发要贴目标、留死区。**
   ε=0.95 意味着 dst 掉到目标的 5% 才触发。
   触发时一次性补满到 τ，撑很久。
   补得少、补得准，relay 反而更低。

## 3 系统结构

每个 broker 进程内一个 `TdrAgent`（状态机）挂在一个动态引擎上。
数据流如下（全本地，无外部提示）：

```
CTX 被本 broker 处理（成交/revert/改判 relay 一视同仁）
   │  observe(Θ1提交块高, dst, 金额)         ← 需求观测（F3 语义）
   ▼
EwmaDemand：每分片速率 r_s，指数衰减，半衰期 hl 块   ← 需求估计
   ▼
compute_target：τ = ρ × λ 份额（q_min 地板）          ← 目标水位
   ▼
topup_plans：只补 actual < τ(1−ε) 的缺口               ← 转移规划
   │  盈余方只出超过自己 τ+ετ 的部分
   ▼
两段落链（与普通 TDR 转移同一条通道）：
   第一段 broker@src → BURN（broker 自签，ledger 预留/扣减）
   第二段 coordinator 代铸 → broker@dst（回执闭环）
```

### 3.1 事件状态机（tdr_engine.py）

三条纪律不变（论文假设 C4）：
- 事件互斥：同一 broker 至多一个活跃事件。
- χ 块最小间隔：`block ≥ last_event_block + χ`。
- 超时不重试：首段超时记 lost_in_transit，显式入账。

EWMA 版冷启动保护：见过 ≥5 个不同块才允许规划
（替代硬窗口的"满窗"条件）。

### 3.2 需求观测的记账位置

每笔被本 broker 经手的 CTX，在 Θ1 提交块高回看记入。
成交、revert、改判 relay 都算需求（路由时刻语义）。
这保证需求估计包含"被拒的需求"——否则枯竭分片
的需求消失，τ 会自我否定。

### 3.3 块高来源

引擎无全局时钟。每 block_poll_s 轮询本 broker 的两个分片，
取 min 再单调化，作为 h 估计。χ、滑窗都以块计。

## 4 纯函数定义（tdr_policy.py）

### 4.1 EwmaDemand(half_life_blocks, min_blocks)

- `observe(block, dst, value)`：先把全部速率按
  `0.5^(块差/hl)` 衰减，再叠加 value。
- `demand(block, n)`：各分片当前速率外推
  `rate × hl`，折算为窗口等价量纲 λ。
- 与硬窗口的本质区别：没有"掉出"事件。
  一笔需求的影响是持续几何衰减，不是悬崖式清零。

### 4.2 compute_target(actual, λ, q_min)

与 proportional 共用的需求份额目标：
τ ∝ λ，整数划分，q_min 地板，Στ = ρ。
ρ = 本 broker 全部可用资金（封闭池，不借钱）。

### 4.3 topup_plans(actual, τ, ε)

- deficit 集合：`actual_s < τ_s − ε·τ_s` 的分片，缺口 = τ−actual。
- surplus 集合：`actual_s > τ_s + ε·τ_s` 的分片，可出 = actual−τ。
- 两侧按金额降序，双指针贪心 `min(头,头)` 配对。
- 与 compute_transfers 的差别：只填 deficit 到 τ；
  surplus 只被抽到 τ 为止；不满足死区的分片完全不动。

### 4.4 语义要点：ε 的双重作用

ε 在这里不只控制"触发"，还控制"谁有资格被补/被抽"。
ε=0.95 时 deficit 条件 ≈ `actual < 0.05τ`——
即分片几乎完全放空才补。surplus 条件 ≈ `actual > 1.95τ`。
一次触发 = 从深度盈余处搬一大笔填深度缺口，一次到位。

## 5 参数表（定稿 + 依据）

| 参数 | 值 | 含义 | 依据 |
|---|---|---|---|
| policy | topup | 只补缺口 | §2 教训 2 |
| demand | EWMA | 平滑需求 | §2 教训 3 |
| hl | 20 块 | 半衰期 | 不敏感（12-40 皆可，档案 §11） |
| ε | 0.95 | 死区（deadband）+ 资格阈值 | 平台 0.95-0.98 且更稳（档案 §18） |
| q_min | 0.10 | τ 地板 | 实测是冷分片保险：0 使 relay 恶化到 131（档案 §18） |
| χ | 10 块 | 事件间隔 | 正式档事件 153，χ 不构成瓶颈 |
| window_blocks | 20 | 硬窗（EWMA 开启时仅兜底） | — |
| timeout_blocks | 20 | 首段超时 | 全档 lost=0 |

**悬崖警示**：ε=1.0 时 deficit 条件变成 `actual<0`，永不成立，
方案静默退化为 plain（档案 §9 实测 relay 3987）。
生产参数取 0.95，距悬崖 0.05。0.97 亦在平台内（样本略差，档案 §18）。
这是当前设计的一个真实脆弱点（见 §8）。

## 6 守恒与安全（机制层不变量）

全部沿用平台既有纪律，方案本身不新增风险面：
- TDR 两段 = burn 自签 + coordinator 代铸，Σburn≡Σmint。
- 全程无 setBalance（审计 F2 红线）。
- 账本 available = confirmed − reserved；进账不参与判定（F5）。
- nonce 单写者：TDR 首段用 broker 本地 nonce，一箱一主。
- 超时不重试，损耗显式入账（C4）。
验证：正式档 gates（G1-G4 + burn≡mint + 逐 broker 账实）全 True。

## 7 与三个对照的结构性差别

| 方案 | 目标水位 | 触发视角 | 动作幅度 |
|---|---|---|---|
| valve | 均匀（init） | 盈余侧堆过 cap | 补到 init 即停 |
| proportional | 需求份额 τ | 盈余侧超 ετ | 全池重排到 τ |
| **topup+EWMA** | 需求份额 τ | **深缺口才动** | **一次整补到位** |

关键区别在最后一列的组合：目标准（需求份额）
+ 出手少（深死区）+ 估计稳（EWMA）。

## 8 已知局限

- ε 贴悬崖，鲁棒性依赖"需求不剧烈换向"。
  需求 regime-shift 流上需重测（未做）。
- 4 分片低集中度场景，本方案不比 valve 省（档案 §3）。
  优势场景是 dst 高度集中 + 多分片。
- 搬量 17 万 ETH 仍是净流量（11.9 万）的 ~1.4 倍。
  不是理论下界。
- q_min 在 ε0.97 下测过 {0,0.05,0.1}；完整联合寻优未做。
  hl 敏感度低，风险小。

## 9 复现命令

矩阵档（快，约 2 分钟/臂）：
```bash
python run.py --set exp.rate=120 --set exp.ctx_per_broker=200 \
  --set exp.pool_scan=50000 --set chain.num_shards=16 \
  --set exp.arms="topup@37.5@@0.95@@20"
```

正式档（约 7 分钟/臂，max_backlog 需随 N 放大）：
```bash
python run.py --set exp.rate=120 --set exp.ctx_per_broker=800 \
  --set exp.pool_scan=200000 --set chain.num_shards=16 \
  --set exp.max_backlog=20000 \
  --set exp.arms="plain@150,valve@150@1.3,topup@150@@0.95@@20,tdr@150@@0.1"
```

token 语法：`name@fund@cap@eps@chi@hl@qmin`（省略位留空占位）。

## 10 代码落点

- 需求估计：`brokerlab/tdr_policy.py` → `EwmaDemand`
- 转移规划：`brokerlab/tdr_policy.py` → `topup_plans`
- 状态机分支：`brokerlab/tdr_engine.py` → `POLICY_TOPUP`
  + `_req_ready`/`_req_demand`（需求源二选一）
- 引擎接入：`brokerlab/tdr_hook.py` → `tdr_engine_from_payload`
- 实验入口：`experiments/exp003_tdr_on_off/run.py` →
  臂 token `topup`、`exp.tdr_ewma_half_life`
- 参数默认：`experiments/exp003_tdr_on_off/config.yaml`
- 测试：`tests/test_tdr_policy.py`（EwmaDemand 4 项、topup 4 项）、
  `tests/test_tdr_engine.py`（topup 状态机路径）
