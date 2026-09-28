# TDR 实验平台彻底重构计划 — `exp_anvil_broker_qwen`

> **状态（2026-09-02）**：M0 归档已完成（三代旧代码位于 `归档/exp_legacy_20260902/`）；**M1 BrokerChain 基座已完成**（用户指示的里程碑重排：先基座、后 TDR——真实 ETH 数据的跨片转账已在 2 分片 Anvil 上跑通并通过全部守恒校验）。
> 致命缺陷排查报告见本目录 [AUDIT_fatal_flaws_CN.md](AUDIT_fatal_flaws_CN.md)（F1–F9，含证据链与修复映射）。
> 本文件为计划唯一权威版本（早期草稿 `~/.claude/plans/floofy-soaring-kahn.md` 已被取代）。

## Context（为什么做这件事）

现有实验代码有三代 Python 实现（`exp/` → `exp_anvil/` → `exp_anvil_broker_process/`），经审计存在致命缺陷（详见 AUDIT_fatal_flaws_CN.md），核心问题：

1. **负载由墙钟定义（F1）**：三代的注入速率/时长/批阻塞都把实验负载绑在机器速度上，η、relay 占比、TDR 增益跨 run、跨机器不可比。
2. **保真度断裂（F2/F6）**：gen-3 的 TDR 再平衡用 `anvil_setBalance` 直改余额——不占区块容量、不产生真实交易，论文开销模型不可测量；且算法（加法目标+固定缓冲）与论文/Go 规格（比例目标+q_min 保底+ετ 触发）不符。
3. **正确性 bug（F3/F4/F5）**：需求统计只记已确认 debit（失衡越重 λ 越被低估，正反馈掩盖 TDR 效果）；队列满 ring 重投导致路由账本失同步；`get_effective_balance` 双计未到账资金、扣款不查预留（ITX2 被偷 ~1/800）、事件无互斥无 χ。
4. **移植障碍（F8）**：Windows 硬编码、全局 pkill、`ETH_cleaned.csv` 路径错位——当前代码在本 workspace 直接崩溃。

**用户决定**：方案 (b)——保留 Anvil 平台，把再平衡改造成真实交易；只重构实验代码（手稿另行处理）；先把实验跑通；旧代码归档到 `归档/`（已完成）；迁移实验室机器前做好移植准备。

**关键参照**：Go 实现 `block-emulator-x-main-TDR/.../block-emulator-x-main/pkg/tdr/`（agent.go / allocation.go / reallocation.go）+ `supervisor/committee/tdrctrl/`（四阶段控制器）是论文算法的正式参考实现。当手稿（用户确认其版本不正确）与 Go 代码矛盾时，**以 Go 代码为准**。用户已确认：触发语义做成配置项，默认 `excess_only`（与 Go 一致），`two_sided`（与手稿一致）保留可选。

## 致命缺陷排查：注入速率污染结果（三代共性，用户指出）

逐代证据（完整链条见 AUDIT_fatal_flaws_CN.md）：
- **gen-1** `exp/runner.py:163-220`：墙钟驱动——`deadline=time.time()+duration_sec`，每 `sleep(ctx_interval_sec)` 注一笔；`periodic` 策略按秒检查（runner.py:194）。负载与策略行为都随机器速度漂移。
- **gen-2** `exp_anvil/run_experiment.py:278-289`：每批 50 笔、批间阻塞等全部回执——每块负载由 RPC 速度内生决定；指标按 batch_idx 而非块高对齐。
- **gen-3** `exp_anvil_broker_process/main.py`：
  1. `target=int(elapsed×CTX_RATE_PER_SEC)`（:304-305）——负载按墙钟定义，CTX/block = rate×block_time 是未声明的自由度；
  2. 路由表每 3 真实秒刷新、串行 800 次 RPC（:245-259）——机器越慢表越陈旧，结果越差；
  3. 队列满 ring 重投 bug（:317 vs :326）——balance_table 扣 `chosen`、交易投进 `alt`，路由账本永久失同步；
  4. 队列全满 `sleep(0.1)` 重试（:337）——实际速率被最慢 broker 钳制，"注入速率"不是自变量。

**为何致命**：TDR 的补液能力按块计（每事件 ≤|𝕊|−1、χ 块一次、2 块确认），排空/补液比值中的分子却由"墙钟×机器速度"决定。η、relay 占比、TDR 增益全部隐含依赖 RPC 延迟与硬件；CLAUDE.md 的 sweep 表（07-20 ≈0 / 07-28 83→87 / 08-01 99.2%）实为不同速率下的不同实验互相比较。**这些旧结果不可引用**（处置清单见审计第五节）。

**修复 = 逻辑时钟（块索引化）**：
- 删除 `CTX_RATE_PER_SEC`，新增 `ctx_per_block`（或到达 profile：constant | poisson(种子) | trace）；协调器在**块边界**一次性释放该块到达，注入只被"新区块"驱动，永不被 `time.sleep` 驱动；
- 慢机器表现为**积压（backlog）**，到达轨迹不变；backlog 是导出指标，并有看门狗：超阈值即中止并标注"硬件不足"，防止欠速 run 被当作负载发现；
- 路由用余额表改为**按块边界刷新**（并行 RPC），废除 3 秒墙钟刷新与 800 串行调用；
- 全部时间量（w、χ、timeout、路由决策、指标键）统一块高——同一 YAML 在任何机器 = 同一逻辑实验。
- 已知残余随机性：单笔交易落入哪个块仍有 RPC 时序抖动——能种则种，跨 run 只比聚合量，靠重复次数消化。

## 实验室机器移植准备

- `pyproject.toml` + 硬 pin `requirements.txt`；`python -m brokerlab doctor` 子命令：检查 Python/web3/eth-account 版本、anvil 存在+版本（`~/.foundry/bin` → PATH）、端口段空闲、`traffic.real_csv_path` 存在、磁盘余量，输出 PASS/FAIL 表；
- 一切路径相对项目根解析，无绝对路径进代码；ETH_cleaned.csv（328MB，现位于 Go 项目目录）经配置引用，迁移时拷 `data/` 即可；
- 进程卫生：只终止自家 PID（run 目录 pids.json）、无 pkill/taskkill、SIGINT 优雅收尾+导出部分结果；
- README 资源需求表：smoke（≈4 进程）/ medium（≈13 进程）/ full（16 anvil + 50 broker + coordinator ≈ 67 进程，估 CPU/RAM），供实验室排期；
- 逻辑时钟（上节）本身就是移植正确性的核心保障；
- M1 骨架落地后即将本项目提交 git（仓库现为空历史），迁移用 clone/scp。

## 已锁定的设计决策

| 决策点 | 结论 |
|---|---|
| 平台 | Anvil × N 分片 + Python 多进程（coordinator + broker 进程） |
| 再平衡链上形式 | 真实交易：第一半 broker→BURN（源分片，broker 进程签名）；第二半 coordinator→broker 子账户（目的分片，coordinator 进程签名，代表 relay 机制）。`setBalance` 只允许出现在一次性 `setup_funding()` 里 |
| TDR 策略 | 论文/Go 规格，可插拔 policy 接口，默认实现 `PaperTDRPolicy` |
| 触发 | 配置项 `trigger_mode: excess_only(默认) \| two_sided` |
| 事件模型 | 每 broker 同一时刻至多 1 个事件；χ 块最小提交间隔；全部确认/超时后才开下一事件 |
| 需求统计 | 在**路由时刻**记录（Go: "successful confirmation is not a prerequisite"），含未服务请求；非 gen-3 的"结算时刻" |
| 负载模型 | **逻辑时钟**：块索引化到达（`ctx_per_block`/profile），无墙钟速率；见上节 |
| 整数化 | 镜像 Go：比例分配向下取整、余数给最大需求分片；地板抬升、超出部分按比例回收、最后一个吸收残差；最终 reconcile 保证 Στ=ρ |
| 匹配 | 双侧列表降序 + 双指针贪心 `min(盈余头, 短缺头)`，≤ n−1 笔（Go computeOptimalTransfers 同款） |
| 超时 | 不自动重试；第二半超时记 `lost_in_transit_wei` 显式入账，守恒检查可见 |
| 规模 | 本机 6C/15G：默认档 smoke(2×2)/medium(4×8) 用 block_time=2s；full(16×50, 12s) 配置保留但在实验室机器上跑 |

## 项目布局

项目根 `/workspace/research_broker_rebalance/exp_anvil_broker_qwen/`，Python 包名 `brokerlab`（CLI：`python -m brokerlab`）：

```
exp_anvil_broker_qwen/
├── README.md  requirements.txt  pyproject.toml   # pip install -e . 消灭 sys.path hack
├── PLAN_refactor_CN.md  AUDIT_fatal_flaws_CN.md  # 本计划 + 审计报告（唯一权威）
├── configs/{smoke,medium,full}.yaml
├── brokerlab/
│   ├── cli.py / __main__.py     # ✅ doctor / demo（M1）；run / check（M3+）
│   ├── config.py                # YAML→frozen dataclass；--set dot-path 覆盖；参数快照→params.json
│   ├── chain.py                 #  salvaged network.py（去 Windows；PID 文件；端口 fail-fast；禁全局 pkill）
│   ├── identity.py              #  salvaged（HD 派生 + per-(addr,shard) NonceManager）
│   ├── tx.py                    #  salvaged injector（sign/send_raw_batch/get_balance/回执三态；set_balance 仅限 setup）
│   ├── matching.py              # ✅ static_broker 匹配策略（纯函数：eligible→种子RNG均匀选；
│   │                            #    顺序实验与 M3 coordinator 共用同一份代码）
│   ├── broker_engine.py         # ✅ 流水线 tick 引擎 + LPT 发送者组装箱（exp005，2026-09-04）；
│   │                            #    M3 broker.py 复用其 pump/poll 逐段语义，替换 intake 为队列、
│   │                            #    叠加 ledger/TDR tick——本文件刻意不含账本/引擎/队列
│   ├── b2e（不独立成文件）       # ✅ config.B2ECfg+b2e_fees（定点整数费用）；
│   │                            #    brokerchain.execute 的 fee_broker_wei/fee_burn_wei+Θ1b；
│   │                            #    实验侧=experiments/exp004_b2e_revenue/（三恒等式全 True）
│   ├── ledger.py                # ✅ BrokerLedger：confirmed/reserved（available=差）；
│   │                            #    incoming 不参与服务判定（审计 F5 防火墙，exp007 起用）
│   ├── tdr_policy.py            # 纯函数：demand/targets/trigger/matching（无 I/O，单测覆盖）
│   ├── tdr_engine.py            # 状态机：需求窗口、χ 计时、事件生命周期
│   ├── broker.py                # broker 进程主循环（块驱动 ITX1/ITX2 流水线 + 引擎 tick）
│   ├── workload.py              # salvaged traffic.py（种子化定向/多对/均匀）
│   ├── real_traffic.py          # salvaged（流式读 ETH_cleaned.csv；路径进配置不进代码）
│   ├── coordinator.py           # 拆解 main.py god-function：启动/充值/生成/**按块释放到达**/路由/监控/收集
│   ├── blockscan.py             # 逐块扫描+按 from/to 分类每笔已挖矿交易（容量 ground-truth）
│   └── export.py                # run 目录 + params.json + 全部 CSV
├── experiments/               # 一实验一包（用户指定的组织方式，2026-09-03）
│   └── expNNN_短主题/         #   README.md（几行说清内容）+ config.yaml（参数&数据源）
│                              #   + run.py（独立启动：执行+绘图）+ out/<ts>/（CSV+图+summary）
│       ├── exp001_relay_vs_broker/
│       ├── exp002_broker_drain_no_tdr/
│       ├── exp004_b2e_revenue/                  # ✅ 2026-09-04（B2E off/on 对照，三恒等式全 True）
│       ├── exp005_broker_substrate_benchmark/   # ✅ 2026-09-04（三容器：进程 3.8×胜出）
│       ├── exp006_rate_invariance/              # ✅ 2026-09-05（逻辑时钟首次压力验证）
│       ├── exp007_dynamic_routing/              # ✅ 2026-09-06/07（M3 验收；改判 27% 守恒零损）
│       └── exp003_tdr_on_off/                   # ✅ 2026-09-07（M4 验收；TDR 把改判压到 0.5%）
├── scripts/{sweep.py, check_run.py}   # M3+ 再建；实验脚本不放这里（归各实验包）
└── tests/test_{tdr_policy,ledger,config,workload}.py
```

配置系统含 `exp:` 直通段（实验自由量，`--set exp.k=v` 类型猜测覆盖）；
trace/ 等相对路径按"向上找 brokerlab/ 或 pyproject.toml"锚定项目根——实验目录可整体搬家。
**参数纪律（2026-09-03 因用户质询确立）**：run.py 内不得保留实验参数的隐藏默认值——
`exp:` 段缺键即 fail-fast 报错，config.yaml 是参数唯一显式来源；旧式"argparse 默认值 +
共享全局配置"的隐式参数布局判为缺陷（scripts/ 旧入口已因此退役删除）。

要求：`web3>=7.16,<8`（代码用 `signed.raw_transaction`，防 v8 漂移）、`eth-account>=0.13,<0.14`、PyYAML、pytest。anvil 经 `~/.foundry/bin`（本机 1.8.1 已装但不在 PATH）。

## 并发与 nonce 所有权（正确性核心）

**每个 (账户, 分片) 恰好一个签名者**——删除第三代的两类跨进程锁：

| 账户 | 签名者 | nonce 来源 |
|---|---|---|
| 用户（ITX1） | broker 进程 | coordinator 注入前按 (sender,src) 预分配（gen-3 已有此机制），broker 只用给定 nonce |
| broker 自身（ITX2、TDR 第一半→BURN） | 仅其 broker 进程，单线程 | 启动时从链上同步 |
| coordinator（relay 第二半、TDR 第二半、用户预充值） | 仅 coordinator 进程 | 启动时从链上同步 |

- broker 需要 coordinator 出手的第二半时，经 `credit_queue` 发 `CreditRequest(kind=relay|tdr, dst, to_addr, amount, ref_id)`；coordinator 执行后经 `credit_ack_queue` 回执 `(ref_id, tx_hash, dst)`。这把 fallback relay 与 TDR 第二半统一成同一条代码路径。
- **Ledger 规则**：`available(s) = confirmed(s) − reserved(s)`，incoming 一律不参与服务判定（修复 gen-3 `get_effective_balance` 引发的 ITX2 冲突）；TDR 第一半即时在源分片 reserve，事件互斥（引擎同时只有一个 active event）杜绝重叠事件双花。
- 队列纪律（gen-3 管道死锁教训）：broker 侧只 `put_nowait`（进度类允许丢弃并计数）；coordinator 每轮循环无条件抽干所有入站队列；broker 顶层 try/except 把 traceback 送 result_queue；coordinator 看门狗（超时 / backlog 超阈值）强制终止并导出部分结果。
- gen-3 的 ring 重投失同步 bug（审计 F4）从结构上消失：不再存在"队列满换 broker"路径——投递滞后只计入 backlog，由看门狗处理，而非静默改道。

### coordinator 块循环（逻辑时钟的宿主）

1. 观察到所有分片高度 ≥ h（新逻辑块边界 h）
2. 按块刷新路由余额表（并行 RPC，每块一次，替代 gen-3 的 3 秒墙钟 + 800 串行）
3. 释放第 h 块的到达量 `arrival(h)`（constant/poisson/trace，种子化）：逐笔选 broker（static_broker：目的分片余额足够者随机取一；无则随机并计 `no_qualified`），进入 coordinator 内存 spool 并按块用 `put_nowait` 投递，未投出的留在 spool 计为 backlog——**到达轨迹（每块宣布了什么）与机器速度无关，投递滞后本身就是欠速信号**
4. 执行 CreditRequest（relay/TDR 第二半）+ ACK 回执
5. 抽干全部入站队列、记录逐块指标
6. 检查看门狗：backlog 超 `max_backlog` 即中止并标注"硬件不足"

## broker 块循环（每块一次）

1. 引擎 tick（检查在途回执 → 推进事件状态机）
2. 提交上块 ITX1 已确认者的 ITX2
3. 抽干 ctx_queue：记录需求 `(h, dst, amt)`（路由时刻，含后续将被 relay 者）；`available(dst)` 够 → reserve + ITX1→broker；不够 → ITX1→BURN + CreditRequest(relay)
4. 等下一个逻辑块边界
5. 查 ITX1 回执 → 成功转入 ITX2 队列；失败/超时 → relay 兜底
6. 查 ITX2 回执 → 结算 ledger、记延迟；失败/超时 → relay 兜底
7. 汇报进度（限流）

## TDR 引擎状态机

`IDLE →(policy 出 Plan 且 χ 满足)→ EVENT_ACTIVE →` 每笔转移：
`FIRST_SUBMITTED(broker→BURN) → FIRST_CONFIRMED → SECOND_REQUESTED →(ACK+回执) DONE | TIMED_OUT`；
全部落定 → `EVENT_CLOSED → IDLE`。χ：`h − last_event_start_block ≥ χ` 才可开新事件。

## 度量与导出

run 目录 `results/<label>_<时间戳>/`：

- `params.json` — 完整解析后配置 + 依赖版本（+git HEAD）
- `summary.csv` — served/relay/η、relay 原因分解（no_balance/itx1_fail/itx2_fail/timeout）、事件数、转移数、超时数、planned/actual moved wei、lost_in_transit、吞吐、墙钟
- `block_metrics.csv` — 逐块：到达宣布数（declared，确定性）、投递数、spool 积压、ITX1/ITX2 提交数、结算 served/relayed、η_t、再平衡半交易数、活跃事件数
- `shard_block_txs.csv` — **逐分片逐块按类别计交易数（itx1_broker / itx1_burn_relay / itx2 / relay_credit / tdr_first_burn / tdr_second_credit / total / tdr_share），blockscan ground-truth 生成**——使论文容量开销可测量
- `ctx_log.csv` — 每笔 CTX 全生命周期（路由、tx hash、落块、延迟块数）——gen-1 的每交易延迟习惯
- `rebalance_events.csv` / `rebalance_transfers.csv` — 按状态机记录（含 tx1/tx2 hash、块号、状态、耗时）
- `tdr_targets.csv` — 每事件每分片 (λ, τ, β, δ)
- `balance_timeseries.csv` / `broker_pnl.csv` / `final_balances.csv` — 沿用 gen-3 加 lost_in_transit
- 日志进 run 目录（不留全局 logs/）

`scripts/check_run.py <run_dir>` 守恒断言（每个里程碑的验收门）：served+relayed==total；Σ broker 终值 == 初值 + gas 奖励 − lost_in_transit；coordinator 支出 == relay 支付 + Σ 第二半；shard_block_txs 计数 ≥ ctx_log 计数；CSV 表头齐全。

## TDR 策略模块要点（纯函数，镜像 Go）

- 需求 λ_s：路由到本 broker、目的为 s、路由块在 [h−w+1, h] 的 CTX 金额和（含未服务者）
- 目标四步（Go computeTargetAllocationForShards 同款）：① Σλ=0 → τ=β 不触发；② 比例 `base_s=ρ·λ_s//Σλ`，余数给最大 λ 分片（同值取最小分片号）；③ 地板 `τ_min=ρ·q_min//n` 抬升 + 超地板者按比例回收、末位吸收残差；④ reconcile：残差加到最大 τ 分片，保证 Στ=ρ
- 触发：`trigger_mode: excess_only`（默认，β > τ+ετ，Go 语义）| `two_sided`（δ⁺>ετ 或 δ⁻>ετ，手稿语义）；触发后全部非零 gap 参与
- 匹配：盈余/短缺列表各按金额降序，双指针 `min(盈余头, 短缺头)`，≤ n−1 笔
- 单测七组断言：Στ=ρ/地板、触发（两模式各测，含"短缺集中而盈余分散"case）、Lemma-1 上界、搬运量=½‖β−τ‖₁、恢复性、确定性、Go 交叉样例 3 组手算

## 里程碑（依赖序；2026-09-02 按用户指示重排：BrokerChain 基座先行，TDR 是叠加功能）

- **M0 归档**（0.5h）：✅ **完成（2026-09-02）**——三代 → `归档/exp_legacy_20260902/`。Go 项目与 ETH_cleaned.csv 原地不动（参照/数据源）。
- **M1 BrokerChain 基座 walking skeleton**（1d）：✅ **完成（2026-09-02）**——包骨架 + YAML 配置（`--set` 覆盖、params 快照）+ `doctor` 体检 + chain/identity/tx/brokerchain/real_data 模块 + 单测 9 项。验收：`python -m brokerlab demo` 在 2 分片 Anvil 上用**真实 ETH 主网交易数据**完成 broker 路径（Θ1 sender→broker@src，Θ2 broker→receiver@dst）与 relay 兜底路径（Θ1→BURN，Θ2 coordinator→receiver）各一笔——全部真实签名/挖矿/回执，8/8 余额差分守恒校验通过，report.json 机器可读，退出后零孤儿 anvil。（合并了原 M1"骨架配置"与原 M2"链上 salvaging"两档。）
- **M1.5 机制对比实验**（0.5d）：✅ **完成（2026-09-03）**——`experiments/exp001_relay_vs_broker/run.py`（一实验一包：run.py+config.yaml+README+out/），50 对真实 trace CTX 双路径各执行一次：H1 延迟相等（broker 1.24s vs relay 1.15s，p50 同 1.045s，hops≈2.0 对称）；H2 relay 跨片通信 2006 B/CTX vs broker 100 B 常数（≈20×；证明换成 RLP+Merkle 下界仍 3–5×）；H3 链上足迹完全对称（各 100 段、分片字节 5383/5382）；100/100 成功、全局净和 0 wei。附：broker 路径净流动 S0→S1 = −41.8 ETH（方向漂移的实测预览，M4 的靶子）。产物 `experiments/exp001_relay_vs_broker/out/<ts>/`（ctx_rows.csv+summary.json+figs/，由新入口重跑生成）。**过程收获**：修复端口探针 TIME_WAIT 假阳性（加 SO_REUSEADDR，附回归测试）；通信度量纪律：payload 字节一律实测、模型常数必须显式声明。
- **M1.6 耗尽基线实验**（0.5d）：✅ **完成（2026-09-03）**——真实数据默认路径迁入 `trace/`（配置 null→项目根解析，相对路径锚定项目根，迁移机器零改动）。`experiments/exp002_broker_drain_no_tdr/run.py`：真实 trace 多数方向流 150 笔（436 ETH）无 TDR 复现手稿 Experiment A，**C1–C4 四结论全部一致**（dst 0.0013 ETH/总零漂移；relay 份额 5→84→97→100%；203→2009 B/笔 9.9×；300 ETH 充值推迟 ×3.36 但仍耗尽），0 失败、burn≡mint 精确对账；产物 `experiments/exp002_broker_drain_no_tdr/out/<ts>/`（含与 fig1 同构图，由新入口重跑生成）。**此为新平台首份可引用实验证据**（口径限制：单进程顺序、无负载压力，属机制验证）。真实 trace 方向占比仅 52%——"定向"来自筛选，论文实验一节须如实说明（手稿"controlled stress regime"定位本来就允许）。
- **B2E 费用机制 + exp004**（1d）：✅ **完成（2026-09-04）**——按 PLAN_b2e v2 落地：
  config.B2ECfg/`b2e_fees`（定点整数）、brokerchain 费用参数与 Θ1b 烧币段、
  exp004 两方案对照（60 笔 S1→S0 流，β=0.10、F=2.1e-3 ETH）：三恒等式 +
  per-broker I2b + 费用完整性全 True；隐含检验成立（on/off 路由序列逐笔一致）；
  exp002 关闭态回归确定性列逐位一致。
- **M2 TDR 策略纯函数 + 单测**（0.5–1d）：✅ **完成（2026-09-07）**——`brokerlab/tdr_policy.py`
  DemandWindow（W 块滑窗，路由时刻记账）+ `compute_target`（Phase A 比例整数划分、余数给最大 λ、
  Phase B q_min 地板、reconcile Στ=ρ）+ `compute_transfers`（excess_only 死区触发、之后 ALL gaps、
  双指针 descending min 配对）——逐语义镜像 Go `pkg/tdr/`；单测 16 项全绿、含 Go 手算交叉样例。
  纯逻辑 + 注入时钟，无需链。
- **M3 多进程块流水线 + 逻辑时钟 + 账本（TDR 关）**：✅ **验收通过（2026-09-07）**——
  证据 exp007（用户 2026-09-06 裁定路由必须动态后落地）：coordinator 进程块循环
  （块驱动释放 + 估计表每块并行刷新 + sender 窗口派发 + nonce 串行预分配 + CreditRequest
  代铸 + rep 回收），50 引擎进程终审；两轮 ×6 方案：G1-G4 全过、declared 回归门 PASS、
  看门狗可中止、smoke 双臂通过。**新发现（写进 M4 动机）**：动态吞吐 = 静态 0.44–0.62×，
  瓶颈是 coordinator 单点环与 27% 终审改判的额外往返——TDR 补液会直接压低改判率。
  原计划要素中动态匹配的终审、账本、逻辑时钟已可运行；spool 投递滞后指标 = backlog 已实现。
  **前置已完成（2026-09-04，用户重排）**：exp005 基底基准——50 broker 流水线 tick 引擎
  三容器对比，进程 3.80×（4.2 核活跃）、线程 0.82×（进程总 CPU ≈ 墙钟，GIL 锁死单核）、
  全部守恒门通过；"每 broker 一进程"由数据裁定成立，`broker_engine.py` 预实现 pump/poll 语义。
  **逻辑时钟首次压力验证（exp006，2026-09-05）**：50 broker × 4 分片 × 1 万笔、按块释放
  （released=块龄×ctx_per_block，不用墙钟）扫 25→300 CTX/块：路由决策指纹与全局守恒跨全速率
  逐位一致（审计 F1 在双路径混合负载下被结构性根除），机器吞吐平台 ≈120 CTX/s、RSS ≈3.9 GB、
  rate≤150 零失败。`broker_engine` 的 release 钩子 + relay 兜底（burn 段引擎自签、mint 段经
  单 coordinator 进程代铸的 CreditRequest 队列）即 M3 块循环与代铸链路的可运行雏形。
  ledger、broker/coordinator 进程、`arrival(ctx_per_block/poisson/trace)` 块边界释放、spool/backlog 看门狗、check_run.py 初版。**"跑通"主里程碑**：smoke 300 CTX 完成且守恒通过；**逻辑时钟回归**：同配置两连跑 declared 到达序列逐块一致；调小 max_backlog 验证看门狗中止。
- **M4 TDR 接入（真实再平衡交易）**（1–2d）：✅ **验收通过（2026-09-07）**——`brokerlab/tdr_engine.py`
  （TdrAgent 事件状态机，IDLE↔EVENT_ACTIVE，χ 间隔/互斥/超时不重试）+ `brokerlab/tdr_hook.py`
  （TdrDynamicEngine 每块 tick：需求按 Θ1 提交块高回看 → τ 目标 → burn+代铸两半段落链）+
  单测 6 项 + 闭环测试。证据 `experiments/exp003_tdr_on_off/` 正式双臂（各 10000 笔同流同 seed）：
  改判 2714→53、成交 7286→9947；773 事件、2294 笔搬运 104764 ETH；0 失败 0 拒答 0 丢失、
  gates（G1-G4+burn≡mint）全 True、net 0 wei；CTX 吞吐 0.912×、e2e p95 1.026×。
  **归因修正（对 M3 的记录）**：relay 压到近零后吞吐不回升 ⇒ 动态吞吐主瓶颈是
  coordinator 单环而非 relay 往返（exp007 README 已注记）。F3 修复验证：tdr 臂需求
  已含被改判/兜底笔，事件 773 全按真实块高回看记账。
  原计划验收项每笔两半有回执、`chi_blocks=1000`→≤1 事件由 tests/test_tdr_engine.py 覆盖。
- **M5 完整度量**（1d）：blockscan ground-truth + `shard_block_txs.csv`/`block_metrics.csv`；scanner 自洽校验。
- **M6 基线对比**（0.5d + 运行时）：medium 同种子 {TDR off/on} 对比表（η、relay 数、TDR 容量占比）。
- **M7 加固与实验室迁移**（0.5–1d）：real_csv 档 medium 跑通；full.yaml 配置校验 + 实验室机器 doctor→demo→smoke 验证序。

总计约 5–7 人日；M3 前不出任何"结果"声称。

## 风险与对策

1. 多进程队列死锁（gen-3 反复踩）→ 队列纪律三条（§并发）+ 看门狗。
2. 端口冲突/僵尸 anvil → 只杀自家 PID；bind-test fail-fast；base_port 进配置。
3. nonce 竞争 → 单写者所有权表是唯一规则，破坏它的新功能一律拒绝。
4. 块时间影响迭代速度与背压 → smoke/medium 用 2s、full 用 12s；逻辑时钟下到达轨迹与块时无关，但每块预算与 RPC 往返压力相关——容量类结论必须整体声明（规模 × block_time × ctx_per_block）三件套。
5. 超时扭曲守恒 → 不重试 + lost_in_transit 显式入账。
6. web3 API 漂移 → 硬 pin v7。
7. 逐块序列随机（时序决定入块）→ 能种则种；跨 run 只比聚合量，README 言明。
8. **速率污染回归**（审计 F1）→ 结构上禁止墙钟注入：代码审查规则——包内除心跳轮询/进度显示外不得出现以 `time.*` 决定负载或策略时机的路径；M4 的到达轨迹一致性测试作为永久回归门。
9. 实验室机器仍可能欠速跑 full 档 → backlog 看门狗给出显式"硬件不足"中止，run 标记为不可比而非静默变形。

## 与手稿的已知出入（留给后续对齐）

- 触发语义：手稿双侧 vs Go 单侧——已做成配置，默认随 Go。正确版手稿到手后核对即可。
- 手稿另含：χ 计时器、q_min 地板、盈亏平衡经济量——本计划已覆盖。
- 摘要 "1,000 brokers" 与正文 "50 brokers" 矛盾等手稿自身问题：不在本次范围（用户决定只重构代码）。
