# B2E（Broker2Earn）手续费机制实现计划

**状态**：已实现，exp004 通过（v2，2026-09-04；实现见当日 commit 前工作树）。
本节 §1–§5 全部落地：B2ECfg + `b2e_fees`（定点整数，恒有 βF+(1−β)F=F）、
`BrokerChain.execute` 费用参数 + Θ1b 烧币段（默认全 0 ⇒ 逐字节走旧路径）、
`experiments/exp004_b2e_revenue/`（off/on 对照，三条恒等式 + per-broker 收入全 True）、
`tests/test_b2e.py`（8 项）。exp002 关闭态回归：确定性列逐位一致（含 C1–C4 再判通过）。
需求：broker 服务一笔 CTX，抽取其 gas 成本
的 β 比例作为收入，β 由参数控制（例如 21000 gas 的 10%）。v2 按用户意见修正：全部 gas
入链，broker 只留小份额 βF，其余烧掉。

## 1. 建模决策：为什么不能走"真 gas"路线

测试床当前 gasPrice=0 是刻意设计（零费用 ⇒ 守恒校验 wei 级精确）。若改用真实 gas：

- 真实链上 gas 费付给**出块者/coinbase**，不付给 broker——这与"B2E 让 broker 从费用中抽成"
  的协议语义无关，模拟它需要给每个分片设 coinbase 账户并处理每块收入归属，把共识层经济
  搅进流动性实验；
- 每笔交易扣费后，exp001/002 已建立的 wei 级守恒等式全部要改成含 gas 项的等式，
  回归代价大、噪声更多。

**结论（2026-09-04 用户修正）：用户全额付 F。βF 搭车在协议转账段内（fee riding）；
(1−β)F 用一笔显式小额交易烧掉。仍不动 gas 层。**
βF 的部分与真实协议同构：费用随交易附挂、由规则路由给 broker。(1−β)F 的显式燃烧是
模拟器对"gas 扣费"的近似——真实链在交易处理内隐含扣 gas，而本测试床 gasPrice=0，
价值移动只能靠显式交易。曾评估 fee-splitter 合约（可省掉这笔额外交易），否决理由：
合约内部分账是 internal transaction，blockscan 不可见，牺牲容量记账的 ground-truth 粒度。

## 1.5 前置：多 broker 下的匹配与收入归属（B2E 的地基）

B2E 的"谁拿到 βF"取决于"谁服务了这笔 CTX"。所以匹配策略必须先于 B2E 定清楚。
本节策略 exp001/exp002/exp004 共用，实现在 `brokerlab/matching.py::select_broker`。

**规则（static_broker，论文 §VI 命名）：**
1. eligible 集合 = 所有"目的分片可用余额 ≥ v"的 broker。
2. eligible 内用**种子 RNG 均匀随机**选一个。（种子进 config.exp.seed，选择序列可复现。）
3. 无 eligible → 返回 None，调用方兜底：顺序实验直接 relay；M3 coordinator 随机指派、
   broker 本地终审后回退。回退计入 `no_qualified`。

**为什么是均匀随机，不是"挑余额最大者"：**
- 均匀 ⇒ 各 broker 可交换，收入/drain/触发率同分布，统计量好解释；
- "挑最有钱"会把 drain 重新分散，等于把半个 TDR 偷偷做进基线，污染"匹配 ⊥ 再平衡"的分层；
- 策略可插拔：日后消融 liquidity-aware 匹配时只换 `matching.py`，实验不动。

**判定分两层，是安全关键：** coordinator 余额表按块刷新（至多陈旧一块），是**估计**；
broker 本地 ledger 才是**终审**。路由估计偶尔偏差只多一次 relay，不会双花（见 exp002
`no_qualified` 列）。

**与 B2E 收入的接缝：** 一笔 CTX 的 βF 归 select_broker 选中的那个 broker。
均匀随机 ⇒ 期望上各 broker 收入相等；跨 broker 收入方差 = 0（同分布）。这条不变式
在 exp004 用 per-broker 收入表实测核对。num_brokers=1 时匹配层退化，与现有 exp001/002
逐位一致（已回归验证）。

## 2. 机制定义

三个参数（`b2e:` 配置段）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `enabled` | false | 关闭时行为与现状逐字节一致（回归不变式） |
| `fee_share` β | 0.10 | broker 抽取比例（用户表述的"10%"） |
| `gas_units_per_ctx` | 21000 | 一笔 CTX 的 gas 成本基数（用户表述的"21000"） |
| `ref_gas_price_wei` | 1 gwei | 名义 gas 单价：费用计价基准（真实 gas 仍为 0；固定值，无浮动机制——用户 09-04 确认） |

**费用基数** `F = gas_units_per_ctx × ref_gas_price_wei`（如 21000×1gwei = 2.1e-5 ETH）。
用户每笔 CTX **全额支付 F**。broker 份额 `βF = ⌊β·F⌋`，网络份额 `F − βF` 烧掉。

每条段的资金流（用户修正模型，两路径用户成本对称 = v + F）：

```
broker 路径（服务成功）                            relay 路径（回退）
Θ1a: sender → broker@src,   v + βF                 Θ1: sender → BURN@src, v + F（fee 随 v 同烧）
Θ1b: sender → BURN@src,     (1−β)F  ← 额外小额交易  Θ2: coordinator@dst → receiver, v
Θ2:  broker@dst → receiver, v                      broker 分文不进
broker 净收入 = +βF                                 BURN 合计 = v + F
BURN 合计 = (1−β)F
```

Θ1b 只在 broker 被使用时出现（每笔 +1 链上交易，src 分片本地、不跨片，
不进通信账）。relay 路径不额外加笔：F 本来就得和 v 一起进 BURN。

守恒恒等式（升级为 exp001/002/004 通用检查）：
1. Σ全部受监控账户净和 = 0（含 BURN）——结构不变；
2. `Δbroker_total == Σ_{served} βF`（流动性 v 进出互相抵消，只有 βF 净流入）
   ——这是 B2E 收入的链上审计凭据；
3. `ΔBURN == Σ_{relay}(v+F) + Σ_{served}(1−β)F`——燃烧台账闭环。

## 3. 代码改动（分层，各 ~半小时级）

1. **`brokerlab/config.py`**：新增 `B2ECfg` frozen dataclass（上述 4 字段）并入 `RunCfg`/
   `_SECTIONS`；校验 β∈[0,1]、units>0、price≥0；helper `b2e_fees(cfg) -> (betaF, oneMinusBetaF)`。
   params.json 自动快照。默认 `enabled:false` → 现有实验结果不受影响。
2. **`brokerlab/brokerchain.py`**（机制层，保持无政策）：`execute(ctx, route,
   broker_idx, *, fee_broker_wei=0, fee_burn_wei=0)` ——broker 路径 Θ1a=`v+fee_broker`、
   紧随一笔 Θ1b sender→BURN=`fee_burn`；relay 路径 Θ1=`v+fee_broker+fee_burn`（全额随 v 烧）。
   `CtxResult` 记录各段金额与 tx hash。**费用如何拆分是调用方政策**（查 `B2ECfg`），
   机制层只按参数搬运价值。Θ1b 顺序：与 Θ1a 同 sender 同分片、nonce 递增、各自等回执。
3. **实验驱动器**（run.py 侧）：从 `cfg.b2e` 算 βF/(1−β)F 传给 execute；CSV 新列
   `fee_broker_wei, fee_burn_wei`；summary 新增 `b2e_revenue_wei`（ΣβF served）、
   `b2e_burned_wei`（Σ(1−β)F served + ΣF relay）、三条恒等式核对（§2）。
4. **回归门**：`b2e.enabled=false` 重跑 exp002，route/amount/balance 列与
   `experiments/exp002_broker_drain_no_tdr/out/20260904_024358` 逐位一致（时间戳列允许抖动）。
   启用时额外断言：
   全局净和仍 = 0，且 broker 路径每笔恰好多一条 BURN 段（链上计数核对）。

## 4. 新实验包 `experiments/exp004_b2e_revenue/`

一实验一包（README+config.yaml+run.py+out/，参数全显式+中文注释）。负载复用
exp002 的构造（真实多数方向定向流），两组：`b2e.off` 与 `b2e.on`（β=0.10，
ref_gas_price 提到实验可读档，如 1 gwei→100 gwei 由 config 显式声明）。输出：

- 图：(a) broker 累计收入阶梯线 vs 服务数；(b) served/relay 柱 + 燃烧份额（右轴）；
  (c) 三条恒等式核对表（Δbroker ≟ ΣβF、ΔBURN ≟ 燃烧台账、全局净和 ≟ 0）；
- summary：每组 revenue/burned/identity，β 敏感性一行表；
  per-broker 收入表（§1.5 接缝的实测：均匀随机下跨 broker 收入差 → 0 随笔数增长）。

**为什么值得单开实验**：B2E 收入参数 κ 是论文盈亏平衡分析（§V break-even）的收入端；
exp004 先把收入端的账立起来，M4 的 TDR 才有成本-收益两端可算。

## 5. 测试清单

- `test_b2e_fee`：β=0 → broker 0/烧 F；β=1 → broker F/烧 0；舍入=floor；
  恒有 `fee_broker + fee_burn == F`（拆分不丢 wei）；F=units×price 用 int 防溢出；
- `brokerchain` 费用搬运：demo 级 2 分片小链跑一笔 broker CTX，断言 sender −(v+F)、
  broker src +v+βF、dst −v、BURN +(1−β)F；再跑一笔 relay，断言 BURN +(v+F)、broker 不变；
- exp004 内三条恒等式（§2）全部成立；
- 回归门（§3.4）：β=0 关时逐位复现旧结果。

工作量：机制+配置 0.5 天；exp004 包 0.5 天（含图）。

## 6. 决策记录（2026-09-04 用户答复）

(a) relay 路径用户成本与 broker 路径对称（都是 v+F）：**同意**（修正后 relay 侧是全额 F 烧掉）。
(b) **用户否决了"网络份额只文档化"的初稿方案**。修正模型：每笔 CTX 的全部 gas 都真实
    入链——broker 服务时 βF 给 broker、(1−β)F 显式烧掉；relay 时全额 F 烧掉。
    网络份额不再悬空。已按此重写 §1–§3。**定案。**
(c) ref_gas_price：**用户确认固定值**，不实现以太坊浮动 gas price 机制。
    默认 1 gwei；实验配置可显式提档并在 README 注明。
(d) `b2e.charges_rebalancing` 开关（TDR 再平衡交易同样付费，为 break-even 的
    成本端 μ 提供可测实现）：**同意**——留配置字段，M4 实验时启用。
