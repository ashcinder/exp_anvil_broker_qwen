# BrokerChain/TDR 测试床 · 项目总结文档

> 项目目录：`exp_anvil_broker_qwen`
> 一句话定位：**一个在本地 Anvil 多分片 EVM 上用真实以太坊主网交易数据，复现并验证 BrokerChain 论文（broker 机制、relay 兜底、TDR 交易驱动再平衡、B2E 手续费）的实验平台。**
> 本文档汇总"项目是什么、每个文件夹什么意思、怎么跑、8 个实验各干什么"，是对仓库内各 `*_CN.md` 文档的浓缩索引。

---

## 1. 这个项目到底是干什么的

### 1.1 研究背景（问题从哪来）

- **BrokerChain** 是一种多分片以太坊的执行架构：链被切成 N 个分片（shard），每个分片是一条独立链。
- 系统里有一批 **broker**，每个 broker 用同一条地址在**每个分片**各持有一个**子账户**（sub-account，即"该地址在该分片的余额 + nonce"）。
- 用户要跨分片转账时，**匹配层**把请求路由给某个 broker；broker 用自己的两侧子账户垫付资金，分**两段链上交易**完成转账：
  - **Θ1（源分片段）**：发送者 S → broker 在源分片的子账户，付 v。
  - **Θ2（目的分片段）**：broker 在目的分片的子账户 → 收款人 R，付 v。
- 真实流量是**有方向性**的（钱总往少数热门分片流）。于是 broker 在目的分片一侧的子账户会被**持续抽干**，这个现象叫 **broker 资金耗尽（drain）**。
- 目的分片余额不够时，系统**回退（fallback）到 relay 机制**：
  - **Θ1**：S 把 v 打进 BURN 死地址（源分片"销毁"）。
  - **Θ2**：coordinator（目的分片委员会的替身）在目的分片真实转账给 R（代表"铸造"）。
  - relay 不消耗 broker 流动性，但跨片消息贵得多（要搬运 Θ1 的入块证明，实测约 2 KB/笔 vs broker 的 100 B 控制消息）。
- 本研究提出的对策叫 **TDR（Transaction-Driven Rebalancing，交易驱动再平衡）**：跑在 broker 钱包内部、不改协议，按近期需求把资金在 broker 的各子账户之间"搬平"，从而避免被抽干、减少回退到昂贵的 relay。

### 1.2 这个平台做什么

它把上面这套机制做成**可真实执行、可精确对账、可复现**的实验环境：

1. 用 **Anvil（Foundry 的本地以太坊节点）**，一个进程模拟一个分片，起 N 个进程就是 N 分片。
2. 用 **ETH 主网真实交易数据**（`trace/ETH_cleaned.csv`，约 130 万行、328 MB）流式筛选出跨分片转账请求（CTX），地址到分片/账户的映射规则与旧三代代码逐字节一致。
3. 每笔交易都是**真实签名、真实挖矿、有回执**的链上交易；`gasPrice=0` 使转账免费，守恒校验能做到 **wei 级精确**。
4. 用一系列 **守恒门（G1–G4 + burn≡mint 台账）** 自动核对：钱分毫不差、无重无漏、账本 == 链上、全局净和 0 wei。
5. 每个实验一个文件夹、一个 config、一个 run.py，**退出码 = 判据是否全部成立**，产物落在 `out/<时间戳>/`。

### 1.3 与旧代码的关系

平台是**推翻重写（ground-up refactor）**。旧三代代码有 9 项致命缺陷（见 `AUDIT_fatal_flaws_CN.md`，编号 F1–F9），例如：负载由墙钟定义（F1）、再平衡用 `anvil_setBalance` 魔法改余额不产生真实交易（F2）、需求统计在失败最严重时失明（F3）、账本双计/预留窃取（F5）等。旧三代所有数字已作废（F9），本平台产出的才是**首份可引用证据**。

---

## 2. 核心术语速查

| 词 | 含义 |
|---|---|
| **分片 shard** | 一条独立链；本平台里就是一个 anvil 进程 |
| **CTX** | 一笔原始跨分片转账请求（还没上链） |
| **Θ1 / Θ2** | 服务一笔 CTX 的两段链上交易：源分片段 / 目的分片段 |
| **broker** | 持多分片子账户、用自己的钱垫付的中介 |
| **relay** | 兜底机制：burn-and-mint（销毁 + 铸造），不耗 broker 流动性但跨片消息贵 |
| **burn / mint** | 销毁 / 铸造；relay 与 TDR 的两半段语义 |
| **drain** | broker 目的分片子账户被定向流抽干 |
| **TDR** | Transaction-Driven Rebalancing，交易驱动再平衡 |
| **B2E** | Broker2Earn，broker 手续费收入机制 |
| **coordinator** | 路由/代铸的中央协调进程（代表目的分片委员会） |
| **sub-account** | 同一地址在单分片上的余额与 nonce |
| **逻辑时钟** | 一切时间量按"块索引"计，禁用墙钟决定负载/策略时机 |
| **η（服务率）** | served 笔数 ÷ 总笔数 |
| **守恒门** | 自动核对：落定零失败 / 无重无漏 / 账实一致 / 净和 0 |

---

## 3. 目录结构：每个文件夹是什么意思

### 3.1 顶层文件

| 文件 | 作用 |
|---|---|
| `README.md` | 运行入口、安装方法、各实验一句话结论、进度清单 |
| `CODE_READING_GUIDE_CN.md` | **逐文件代码阅读路线**（审核入口，建议从这里读代码） |
| `EXPERIMENT_DESIGN_CN.md` | **实验设计文档**：回答"为什么这样设计"（平台实现、机制、红线、口径） |
| `EXPERIMENT_REPORT_CN.md` | **exp001–exp008 全部实验报告**（结论汇总，最权威） |
| `PLAN_refactor_CN.md` | 里程碑与设计决策权威（重构计划） |
| `PLAN_b2e_CN.md` | B2E 手续费方案（已实现） |
| `AUDIT_fatal_flaws_CN.md` | 旧三代代码的 F1–F9 致命缺陷清单（本文的红线出处） |
| `pyproject.toml` / `requirements.txt` | 包定义与依赖（web3、eth-account、PyYAML、pytest） |

### 3.2 顶层文件夹

| 文件夹 | 含义 |
|---|---|
| `brokerlab/` | **核心 Python 包**（共享底座）：链管理、交易、机制、账本、TDR、引擎、CLI 全在这 |
| `experiments/` | **一实验一包**，共 8 个实验（exp001–exp008），每个含 README / config.yaml / run.py / out/ |
| `configs/` | 平台级配置档（当前只有 `smoke.yaml`，给 demo 用） |
| `tests/` | **纯函数单元测试**（无需 anvil 即可跑，锁住账本/策略/引擎/映射等不变量） |
| `trace/` | 真实数据 `ETH_cleaned.csv`（328 MB，主网交易） |
| `results/` | M1 demo 的运行产物（`<run>/report.json` 等） |
| `exp_figure_origin/` | **手稿原始绘图代码 + 数据**（一字未动，只读参照） |
| `exp_figure_origin_qwen/` | **用重构实验数据重绘手稿 8 幅图**（`bash run_all.sh` 一键出图） |

### 3.3 `brokerlab/` 包内各模块

按依赖顺序（底座 → 机制 → 引擎 → 入口）：

| 模块 | 层 | 职责 |
|---|---|---|
| `config.py` | 底座 | YAML → 只读 dataclass；全部参数的唯一户口；`--set k=v` 覆盖；校验分片/端口/账户索引 |
| `chain.py` | 底座 | 管理 anvil 子进程集群（启停、端口探测、Web3 连接），只杀自家 PID |
| `identity.py` | 底座 | 从助记词确定性派生账户；按 (地址, 分片) 维护本地 nonce |
| `tx.py` | 底座 | 真实签名转账 + 采集度量（RLP 字节、回执字节、块高）；三态回执 None/0/1 |
| `ledger.py` | 底座 | 三本账（confirmed / reserved / incoming），`available = confirmed − reserved`，防双花 |
| `brokerchain.py` | 机制 | **最核心**：把"一笔 CTX 怎么被服务"写成可执行定义（broker 路径 + relay 路径语义） |
| `real_data.py` | 机制 | 流式读 328 MB 主网 CSV → 过滤、映射出 `RealCtx` |
| `matching.py` | 机制 | 匹配策略 static_broker：目的余额 ≥ v 的 broker 里均匀随机选一个 |
| `tdr_policy.py` | TDR | 再平衡"大脑"：需求滑窗、τ 目标分配、转移规划（逐语义镜像 Go 参考实现） |
| `tdr_engine.py` | TDR | 一个 broker 的 TDR 事件状态机（互斥、χ 间隔、超时归属） |
| `tdr_hook.py` | TDR | 把 TDR 挂进动态引擎（TdrDynamicEngine） |
| `broker_engine.py` | 引擎 | 流水线 tick 引擎（pump/poll，Θ2 只等自己的 Θ1）+ 动态子类 |
| `procmon.py` | 观测 | RSS 资源看门狗（越限中止） |
| `plotting.py` | 观测 | 画图样式（与手稿配色一致，强制 Agg 后端） |
| `cli.py` | 入口 | `doctor` 体检 + `demo` 演示总装 |
| `__main__.py` / `__init__.py` | 入口 | `python -m brokerlab ...` 的入口 |

### 3.4 `experiments/` 里的 8 个实验

| 包 | 测什么（一句话） |
|---|---|
| `exp001_relay_vs_broker/` | broker 与 relay 两机制的延迟 / 跨片通信 / 链上足迹对比 |
| `exp002_broker_drain_no_tdr/` | 无 TDR 时真实数据下的资金耗尽基线（复现手稿 C1–C4） |
| `exp003_tdr_on_off/` | TDR 开/关对照（M4 验收）+ 调度策略全探索（定稿 topup+EWMA） |
| `exp004_b2e_revenue/` | B2E 手续费收入与三条守恒恒等式 |
| `exp005_broker_substrate_benchmark/` | 50 broker 引擎跑在串行 / 线程 / 进程上，选执行基底 |
| `exp006_rate_invariance/` | 注入速率是否会改变结论 + 机器吞吐上限 |
| `exp007_dynamic_routing/` | 动态路由（coordinator 实时选 broker）vs 静态指派 |
| `exp008_tdr_schedule_final/` | 定稿方案正式评估：五方案同场 × 多场，产出论文数据 |

> 编号不是按时间排的：实际完成顺序是 exp001 → exp002 → exp004 → exp005 → exp006 → exp007 → exp003 → exp008。

---

## 4. 8 个实验各自在干什么（内容 + 结论）

> 更完整的数字与逐项表格见 `EXPERIMENT_REPORT_CN.md`。下面按"测什么 / 结论"压缩。

### exp001 · relay vs broker 机制对比
- **测什么**：同一批 50 笔真实 CTX，每条路径各执行一次（逐对交替消序）。broker 路径 = Θ1 用户→broker + Θ2 broker→收款人；relay 路径 = Θ1 用户→BURN + Θ2 coordinator→收款人。
- **结论（三条假设全验证）**：
  - **H1 延迟相等**：broker e2e 1.09 s vs relay 1.12 s（差 2.5%）。
  - **H2 跨片通信差约 20×**：relay 2005 B/笔（实测）vs broker 控制消息 100 B（声明常数）。
  - **H3 链上足迹对称**：各 100 段、每分片字节 5383/5382。relay 的额外开销在跨片消息，不在链上。

### exp002 · 无 TDR 的资金耗尽基线
- **测什么**：150 笔真实定向流（多数方向 S1→S0），单进程顺序执行，两档垫资（100/300 ETH）对照，复现手稿 Experiment A 的四条结论 C1–C4。
- **结论**：dst 分片终值趋零（0.0013 ETH）但总资金守恒；relay 占比沿四分位 5%→84%→97%→100%；每笔跨片字节 202→2009 B（9.9×）；充值 ×3 只把首次回退点推迟 ×3.03（"加钱只买时间"）。**这就是 TDR 要解决的问题。**

### exp003 · TDR 开/关对照 + 调度策略全探索
- **测什么（M4 验收）**：同一条万笔流、同一个 coordinator，两臂各起新链。`plain` 不挂 TDR；`tdr` 每引擎挂 `TdrAgent`（需求滑窗 → τ 目标 → burn+代铸两半段搬平）。后续是**策略探索长任务**（见 `EXPLORATION_log_CN.md` 与 `DESIGN_topup_ewma_CN.md`）。
- **结论**：TDR 把终审改判 relay 从 27.1% 压到 0.5%，成交从 7286 → 9947；773 事件 / 搬运 10.5 万 ETH 下守恒分毫不差。**定稿方案 = topup + EWMA（半衰期 20 块）、ε=0.95、q_min=0.10、χ=10**——只补缺口不抽盈余，用指数平滑替代硬窗口。

### exp004 · B2E 手续费立账
- **测什么**：Broker2Earn 费用机制第一次上链。F=2.1e-3 ETH、β=0.10，用户全额付 F，βF 进 broker、(1−β)F 烧掉。
- **结论（三恒等式全 True）**：全局净和 0；broker 收入 == ΣβF（0.0105 ETH，逐 broker 精确）；BURN 台账闭环。**费用不改变路由序列**（机制与政策分层实证）。

### exp005 · broker 执行基底基准
- **测什么**：50 个 broker 引擎装进哪种容器——串行 / 线程 / 进程，同一引擎同一负载（800 笔）。
- **结论**：串行 24.1 CTX/s（1×）、线程 0.82×（GIL 锁成单核、比串行还慢）、**进程 3.80×**。⇒ "每 broker 一进程"由数据裁定成立，成为后续所有实验的基底。

### exp006 · 注入速率不变性 + 吞吐上限
- **测什么**：50 broker × 4 分片 × 1 万笔，按逻辑块释放，扫 25→300 CTX/块四个速率档。
- **结论**：**决策指纹跨 12 倍速率逐位一致**（速率不改变理论结果，审计 F1 被数据根治）；机器吞吐上限 ≈120 CTX/s；超平台后的代价是延迟（p95 排队）而不是吞吐。

### exp007 · 动态路由（M3 验收）
- **测什么**：路由从启动期装箱改为 coordinator 实时指派（块循环五步：释放/估计表/派发/代铸/回收），50 引擎本地终审，静态臂同会话对照。
- **结论**：约 27% CTX 因估计陈旧被本地改判 relay，改判洪流下守恒零损失；**动态吞吐 = 静态的 0.44–0.62×**，瓶颈在 coordinator 单进程环（后续 exp003 归因：不是 relay 往返，是单环）。

### exp008 · 定稿方案正式评估
- **测什么**：把 exp003 定稿参数固化为可一键复现的生产配置，五方案同场 × 多场，聚合出论文数据。
- **结论（V1/V2/V3 全 True）**：定稿方案（topup+EWMA ε0.95）relay 中位 **20/40000（0.05%）**、搬运 1227 次、搬量 163.7k ETH ≈ 物理下限。比原 proportional：relay 低 5.5 倍、搬运次数省 95%、搬量省 80%。

---

## 5. 怎么运行：从安装到每个实验

### 5.1 安装（一次性）

```bash
# 1) 依赖
python -m venv .venv && source .venv/bin/activate      # 或直接用 conda env
pip install -r requirements.txt && pip install -e .

# 2) anvil（Foundry），代码按 ~/.foundry/bin/anvil → PATH 顺序自动发现
curl -L https://foundry.paradigm.xyz | bash && foundryup

# 3) 体检（迁移/换机器后的第一步）
python -m brokerlab doctor --config configs/smoke.yaml
```

> 真实流量数据 `trace/ETH_cleaned.csv`（328 MB）已在项目内，`traffic.real_csv_path: null` 默认读它。迁移实验室机器 = `git clone` + `rsync trace/`，零配置改动。

### 5.2 先跑一个最小完整演示（M1，2 分钟）

```bash
python -m brokerlab demo --config configs/smoke.yaml
```

它会：起 2 个 anvil 分片 → 从真实交易流式筛 2 笔跨片交易 → 一笔走 broker 路径、一笔走 relay 路径（全部真实签名挖矿）→ 按 (地址, 分片) 逐点核对 8 项余额差分 → 写 `results/<run>/report.json`。

### 5.3 跑单元测试（1 秒，无需 anvil）

```bash
pytest tests/ -q
```

### 5.4 跑每个实验（一实验一包）

每个实验的通用步骤：先 `doctor` 体检该实验配置，再 `cd` 进去跑 `run.py`，**退出码 0 = 判据全部成立**。

```bash
# exp001 机制对比（2 分钟）
cd experiments/exp001_relay_vs_broker && python run.py

# exp002 资金耗尽基线
cd experiments/exp002_broker_drain_no_tdr && python run.py

# exp003 TDR 开/关对照：先冒烟、再正式
cd experiments/exp003_tdr_on_off
python run.py --set exp.ctx_per_broker=2 --set exp.rate=50 --set exp.pool_scan=1500   # 冒烟
python run.py                                                        # 正式：两方案各 1 万笔，≈10 分钟

# exp004 B2E 手续费对照
cd experiments/exp004_b2e_revenue && python run.py

# exp005 执行基底基准
cd experiments/exp005_broker_substrate_benchmark && python run.py

# exp006 注入速率扫描
cd experiments/exp006_rate_invariance && python run.py

# exp007 动态路由
cd experiments/exp007_dynamic_routing && python run.py

# exp008 定稿方案正式评估（多场，较久）
cd experiments/exp008_tdr_schedule_final && python run.py
```

**临时覆盖任意参数**用 `--set 段.字段=值`，例如：

```bash
python run.py --set exp.pairs=20 --set chain.block_time_s=2 --set tdr.enabled=true
```

### 5.5 产物长什么样

每次运行在 `out/<时间戳>/` 下生成：`params.json`（配置快照）、`ctx_rows.csv`（逐笔源数据）、`summary.json`（聚合 + 判据判定）、`figs/*.png`（图）、`logs/`（节点日志）。exp003/008 还有 `tdr_moves.csv`、`coordinator.json`、`report.json` 等。

### 5.6 用重构数据重绘手稿 8 幅图

```bash
cd exp_figure_origin_qwen && bash run_all.sh
```

先由 `prepare_data.py` 从重构实验产物换算数据，再逐图出 PDF/PNG 到各 `*/out/`。

---

## 6. 方法纪律（为什么结果可信）

这是本项目区别于旧三代、也是它能产出"可引用证据"的根基：

| 红线 | 含义 |
|---|---|
| **逻辑时钟** | 负载按块释放（released = 块龄 × ctx_per_block），禁墙钟；审计 F1 的结构性根治 |
| **守恒门 G1–G4** | 全落定零失败 / CTX 无重无漏 / 逐 broker 链上==账本 / 全局净和 0 wei |
| **burn≡mint 台账** | BURN 地址增量 == relay 段 + TDR 段销毁额 == 代铸额 |
| **setBalance 只许 setup** | 实验过程中绝无魔法改余额（审计 F2 根治） |
| **一切度量实测** | 字节、延迟、回执都从真实交易采集；唯一模型常数是 broker 控制消息 100 B |
| **单写者 nonce** | 一个 (账户, 分片[, nonce]) 恰好一个签名者 |
| **参数唯一来源** | config.yaml；run.py 缺键即报错 |
| **只杀自家子进程** | pids.json；审计 F8 根治 |
| **失败 vs 缺失可区分** | 回执三态 None/0/1 |

---

## 7. 当前进度与论文覆盖

### 7.1 里程碑（摘自 README 进度清单）

- ✅ M0–M4 完成（基座演示 → 机制对比 → 耗尽基线 → 多进程流水线/逻辑时钟/账本 → TDR 接入）
- ✅ B2E 费用机制 + exp004
- ✅ exp005–exp008 全部完成
- ⬜ M5 blockscan 容量度量（逐块扫描分类，产出 ground-truth 区块占用）
- ⬜ M6 基线 vs TDR 对比（η/容量占比口径）
- ⬜ M7 实验室机器 full 档验证（16 × 50，12s 块）

### 7.2 对原稿实验节的覆盖（`EXPERIMENT_REPORT_CN.md` §11）

原稿五项实验 A–E：A（无 TDR 基线）**完整**；B（TDR 有效性）**完整且超出**；C（系统开销）**部分**（缺"每分片最大区块占用"需 M5、"broker 净收益"需 TDR 收费口径）；D（参数敏感性）**完整**（矩阵档为主）；E（混合负载）**部分**（缺时变 regime-shift 流）。

### 7.3 对论文叙事的两点提醒（作者自己下的结论）

1. **优势场景要写明**：定稿方案赢在"多分片 + dst 高度集中 + 资本受限"；单热分片/低集中度场景里 valve 级别已够且更便宜。
2. **吞吐不进卖点**：主指标是服务率、relay、搬运量（次数）、守恒；吞吐损失是 coordinator 单环/共享资源，与 TDR 机制无关。

---

## 8. 建议的阅读顺序（接手人用）

1. 本文件（项目全貌）→ `README.md`（入口 + 结果摘要）
2. `EXPERIMENT_DESIGN_CN.md`（为什么这样设计）→ `EXPERIMENT_REPORT_CN.md`（8 个实验结论）
3. `CODE_READING_GUIDE_CN.md`（逐文件读代码的路线）
4. 亲手跑一遍：`doctor` → `demo` → `pytest` → `exp001`（README §8 就是这个建议）
5. `AUDIT_fatal_flaws_CN.md`（理解 F1–F9 为什么必须重写）→ `PLAN_refactor_CN.md`（里程碑决策）

---

*本文档为仓库内各 `*_CN.md` 的浓缩索引，数字与结论以各实验包 `out/<ts>/summary.json` 及 `EXPERIMENT_REPORT_CN.md` 为准。*
