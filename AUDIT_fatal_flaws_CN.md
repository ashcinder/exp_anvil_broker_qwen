# TDR 实验平台致命缺陷审计报告

**审计日期**：2026-09-02
**审计对象**：已归档的三代 Python 实验代码，路径均相对 `归档/exp_legacy_20260902/`
（gen-1 = `exp/`，gen-2 = `exp_anvil/`，gen-3 = `exp_anvil_broker_process/`）
**方法**：全文通读三代注入/平衡/账本/度量代码，逐条核对与论文/Go 参考实现的语义差异，并核对已产出结果的口径一致性。
**参照**：论文算法正式实现 = Go `block-emulator-x-main-TDR/.../pkg/tdr/` + `supervisor/committee/tdrctrl/`；手稿（用户提供版，已确认非正确版本）仅作辅助。

---

## 一、结论摘要

| # | 缺陷 | 波及代 | 等级 | 后果 |
|---|---|---|---|---|
| F1 | 负载由墙钟定义（速率/时长/批阻塞） | 三代全部 | **致命** | η、relay 占比、TDR 增益隐含依赖机器速度与 RPC 延迟；跨 run、跨机器不可比 |
| F2 | 再平衡不产生任何链上交易（`anvil_setBalance` 魔法） | gen-3 | **致命** | 论文核心贡献之一（容量开销 Prop.1、Γ、半交易计量）在此平台上物理不可测 |
| F3 | 需求统计盲：只记"已确认的 debit"，未服务/被 relay 的路由不计入 | gen-3 | **致命** | 失衡越严重 λ 越被低估 → 正反馈性掩盖 TDR 效果；与论文 Phase 1 定义直接矛盾 |
| F4 | 队列满 ring 重投：记账对象与投递对象分离 | gen-3 | **致命** | coordinator 路由余额表与真实余额永久失同步 |
| F5 | `get_effective_balance` 把未到账 credit 当可用、扣款不查预留 | gen-3 | **致命** | TDR 可搬走 ITX2 已预留资金（实测 ~1/800 失败）；事件可重叠，违反论文 C4 |
| F6 | 策略公式与论文/Go 规格不符（加法目标、固定金额缓冲） | gen-2/3 | **致命** | 参数扫描结果无法与 Lemma 1 / Prop. 1 对照；ε 的量纲随规模漂移 |
| F7 | 无全局块索引：块循环键于"任一分片前进"，窗口键于 max(各分片高度) | gen-3 | 严重 | η^t、w、χ 的"块"语义模糊，分片漂移即产生口径漂移 |
| F8 | 启动前全局 `pkill -f anvil` / `taskkill` | gen-3 | 严重 | 共享实验室机器上会杀别人的进程；移植必除 |
| F9 | 历史结果口径污染 | gen-3 结果目录 | 结论 | **所有旧 sweep 数字作废，不可引用**（见第五节） |

---

## 二、致命缺陷详述（证据链）

### F1 墙钟负载——三代同病，形态各异

- **gen-1** `exp/runner.py:163-220`：`deadline = time.time() + duration_sec`，循环内 `time.sleep(ctx_interval_sec)` 注一笔。同样配置下，机器越快、RPC 越利索，落入每块的 CTX 越多。`periodic` 再平衡策略同样按**秒**检查（runner.py:194-195），触发时机也是机器的函数。
- **gen-2** `exp_anvil/run_experiment.py:278-289`：每批 `BATCH_SIZE=50` 笔（config.py:89），`execute_pipelined(batch)` 阻塞至全部回执再进下一批——每块负载 = 批大小 ÷ (批耗时/块时)，完全由 RPC 速度内生决定；度量还按 `batch_idx` 快照而非块高（:289）。
- **gen-3** `exp_anvil_broker_process/main.py:303-337`：
  1. `target = min(len(ctx_list), int((time.time()-start_time) × CTX_RATE_PER_SEC))`（:304-305）——名义"每秒 N 笔"，实际 CTX/block = rate × block_time，是一个**从未被声明的实验自由度**；
  2. 队列全满时 `sleep(0.1)` 后重试（:336-337）——实际注入节奏被最慢的 broker 钳制，"注入速率"从自变量退化为因变量。

**为什么致命**：TDR 的补液能力按块计（每事件 ≤|𝕊|−1 笔、χ 块一次、两半共 ~2 块确认）。决定实验结局的"排空速率 ÷ 补液速率"比值，分子端却由"墙钟 × 机器速度"决定。于是服务率、relay 占比、TDR 增益全部随 RPC 延迟和硬件漂移——同一份配置在两台机器上是两个实验。这直接封死了论文的 A/B 主张和一切跨 run 对比。

### F2 再平衡零交易化

`core/tdr.py:61-66, 136-143`：扣款和入账全部经 `injector.set_balance()`（→ `anvil_setBalance` RPC 直改链上余额，injector.py:169-173），**没有任何一笔被挖矿的交易**；成本只是账本常数（:140, :153-154）。后果：
- 区块永不被再平衡占用 → 论文用整节论证的容量开销模型（每事件 2(|𝕊|−1) 个半交易、Γ 上限、Prop. 1 平均占用、逐分片峰值式 (eq:per_shard_overhead)）**没有对应可观测量**；
- 通信量/带宽类指标同样无从产生；
- 摘要里"parameter-controllable theory-guaranteed overhead on block capacity"在此平台上是空话。

### F3 需求统计在系统最失败时失明

`core/broker_worker.py:304-307`：`record_block_debit()` 只在 **ITX2 确认成功**后记账。两处系统性偏差：
1. 被 relay 兜底的 CTX（余额不足 :240-246、ITX1 失败 :272-279、ITX2 失败 :309-314）对 λ 贡献为零；
2. 排队未处理/在途的也不计。

而手稿 Phase 1 白纸黑字要求包含未服务的路由请求（main.tex §IV-C："The set includes raw TXs that the broker could not serve and that later used fallback relay"），Go 参考亦明确"successful confirmation is not a prerequisite"（agent.go ObserveCTXAt 注释）。
**致命机制**：目的分片越枯竭 → 失败越多 → λ 越少 → TDR 目标越保守 → 越不补液。正反馈在失败最严重时掩盖了 TDR 本应发挥的作用——这正是 TDR=0/TDR=1 增益≈0 的候选解释之一。

### F4 ring 重投的账本错位

`main.py:311-319` vs `:322-327`：余额扣减落在 `balance_table[chosen][dst] -= amt`（:317），但 `put_nowait` 因队列满改投到 `alt = (chosen+retry_bid) % NUM_BROKERS`（:324-326）——**扣了 A 的账，活派给了 B**。此后 A 被路由得比真实余额乐观、B 被路由得比真实余额保守，且永不回滚；叠加 F1 的 3 秒刷新窗口，路由决策与链上真实分布脱钩。

### F5 账本双计与预留窃取

`core/broker_state.py:30-35`：`get_effective_balance = balances − pending_debits + pending_credits`。
- TDR 规划用"有效余额"（tdr.py:103），其中 `pending_credits`（服务在途、尚未上链确认的钱）**被当成可动用资金**参与目标分类；
- 扣款时（tdr.py:136-143）只对 `injector.get_balance()` 的链上值做减法，**完全不查 `pending_debits`**——ITX2 已预留的资金可以被搬走 → CLAUDE.md 自认的 "ITX2 Insufficient funds ~1/800"；
- `rebalance()` 每个块循环开头无条件调用（broker_worker.py:199），`tdr_pending_credits` 是单一字典持续叠加（tdr.py:144）——**无事件原子性、无 χ、无互斥**，前一事件未落定即可再开新扣款，直接违反论文假设 C4（one event at a time）。

### F6 策略公式走样

`config.py:73-76` + `core/tdr.py:81-107`：
- 目标 = `floor_wei + demand[s]`（加法、绝对 wei），非论文的 τ̃ = ρ·λ/Σλ（比例）；
- 触发缓冲 = `BROKER_INITIAL_BALANCE_WEI × threshold`（**初始**余额的固定金额），非 ε·τ_s（当前目标的比例）——同一 ε 在不同分片资金规模下含义漂移，参数扫描与理论无法对照；
- 触发虽名为"双侧"（surplus/deficit 分类），但两侧判据不对称（一侧带 buffer 一侧不带），与手稿式 (eq:two_sided_trigger) 和 Go `computeOptimalTransfers` 均不一致。

### F7 全局块索引缺失

`core/broker_worker.py:125-143`：块循环以"**任一**分片出新块"为 tick；`core/tdr.py:74-75`：窗口以 `max(current_blocks.values())` 锚定。16 个独立 anvil 相位各移时，"第 t 块"没有全系统一致的含义，η^t、w、χ 的计量都在漂移上叠加漂移。新设计必须以"所有分片高度 ≥ h"为逻辑块边界（见计划 coordinator 块循环）。

### F8 进程卫生（移植隐患）

`main.py:48-57`：启动前 `pkill -f anvil` / `taskkill /IM anvil.exe` **全局**杀进程；实验室共享机器上这是事故源。另有 `config.py:89` `GANACHE_DATA_DIR = "E:/ganache_data"`、`real_traffic` 的 CSV 路径拼在自身目录（而 `ETH_cleaned.csv` 实际在 Go 项目内）→ 当前代码在本 workspace 直接崩溃。

---

## 三、严重但非致命

1. **路由余额表**：3.0 秒墙钟周期 + 串行 800 次 RPC（main.py:245-259）——刷新耗时随机器变，表陈旧度随之变（F1 的放大器）。
2. **随机不可复现**：路由 `random.choice`（main.py:316）与随机指派（:319）无种子；仅合成流量生成器带 seed=42。
3. **无参数快照**：结果目录名只编码 4 个 sweep 键，其余全部生效参数无记录（gen-1 的 `params.json` 好习惯在 gen-3 退化丢失）。
4. **gas 奖励只记本地账本**（broker_worker.py:304-305 `confirm_credit(src, BROKER_GAS_REWARD_WEI)`）——链上 broker 总额与本地账本从第一笔服务起分叉，守恒核对必须双轨。
5. **coordinator 50K ETH/分片垫付**无计量上限告警：长 relay-heavy run 会静默耗尽 → 兜底失败被记为普通发送失败。
6. **死码与断测**：`core/relay.py` 整文件无人引用；`test_pipeline.py` 用旧版 `run_broker` 签名（多传 `balance_report_queue`、`global_demand_shares`），现调用必 TypeError；`CLAUDE.md` 描述（单阶段/全局需求份额）与 7/30 代码（两阶段/本地需求）相反。
7. **gen-2 静态预注入**（其 CLAUDE/代码注释自述）：全部 CTX 在 broker 启动前按静态预算分配完毕，无运行时反馈——与 gen-3 是两种不同的负载模型，两者结果不可互比。

---

## 四、缺陷 → 新平台修复映射

| 缺陷 | 新设计对应 | 回归门 |
|---|---|---|
| F1 墙钟负载 | 逻辑时钟：`arrival: constant/poisson/trace` 按块释放，coordinator 块循环，无 `ctx_rate_per_sec` | M4：同配置两连跑，`block_metrics.csv` declared 序列逐块一致；审查规则"包内禁止以 time.* 决定负载/策略时机" |
| F2 setBalance 魔法 | 再平衡=真实两半交易（broker→BURN / coordinator→broker）；setBalance 仅限 setup_funding | M5：每笔转移两半均有回执；M6 blockscan ground-truth 容量表 |
| F3 需求盲 | 路由时刻记录，含未服务请求 | M3 单测 + M5 检查 λ 与路由数守恒 |
| F4/F5 双花与错位 | 账本三本账 `available=confirmed−reserved`（incoming 不参与）；单写者 nonce 所有权；事件互斥 | test_ledger 不变量；check_run 守恒断言；M5 chi=1000 ≤1 事件 |
| F6 公式走样 | tdr_policy 镜像 Go（比例+地板+余数规则+双指针） | M3 七组断言含 Go 交叉样例 |
| F7 块模糊 | 逻辑块 h = 全分片高度 ≥ h | coordinator 块循环定义即修复；指标键=块高 |
| F8 移植卫生 | pids.json 只杀自家；doctor 体检；路径全进配置 | M1 `doctor` 全 PASS；M2 `pgrep -c anvil == 0` |

---

## 五、历史结果处置（F9）

以下数字**全部作废、禁止引用**（`归档/exp_legacy_20260902/` 保留原样仅供追溯）：

| 出处 | 数字 | 作废原因 |
|---|---|---|
| CLAUDE.md 扫描表 07-20 四行 | 38.4/38.5、96.5/96.5、88.5/88.3、60.0/60.1 | gen-2 静态注入 + F1/F2（批阻塞内生负载）；F6 |
| results/ENABLE_TDR=0 vs 1（07-28） | 83.1% vs 87.0% | 同 run 内速率相同是其唯一可取之处，但仍受 F2/F3/F5 污染，只可作"方向参考"，不可作数据 |
| results/*_TDR_WINDOW=*（07-28/29） | 窗口扫描系列 | F1/F3/F6；且 run 间 CTX 总量与 CSV 数据源口径未记录 |
| results/*CTX_COUNT=50000_TDR_WINDOW=5*（08-01） | 99.2% | 负载口径与 07-28 不可比（F1），99.2% 是"低压力档下的天花板"，与基线 83-87% 不构成对照 |

**执行纪律**：新平台产出结果之前，论文实验一节不得引用任何现有数字；`results/` 与 `data/` 旧目录随三代代码一并归档，不再更新。
