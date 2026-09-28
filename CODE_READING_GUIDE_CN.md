# 代码阅读指南 · brokerlab 测试床（逐文件导览）

**文档日期**：2026-09-05。
**用途**：引导你按最容易理解的路径，一个文件一个文件地审核全部代码。
**配套**：全部函数与变量都已配中文注释（标识符保留英文）。本文讲"读什么、为什么、审什么"。

**先懂三个词**：
- CTX：一笔原始跨分片转账请求（还没上链）。
- Θ1 / Θ2：服务一笔 CTX 的两段链上交易。Θ1 在源分片，Θ2 在目的分片。
- anvil：本地以太坊节点程序。一个分片就是它的一个进程。

---

## 0 全局地图（先花 10 分钟）

### 0.1 目录结构

```
exp_anvil_broker_qwen/
├── pyproject.toml / requirements.txt   包定义与依赖 pin
├── README.md                           运行入口与结果摘要
├── EXPERIMENT_DESIGN_CN.md             实验设计文档（回答"为什么这样设计"）
├── PLAN_refactor_CN.md                 里程碑与决策权威
├── PLAN_b2e_CN.md                      B2E 手续费方案（已实现）
├── AUDIT_fatal_flaws_CN.md             旧三代缺陷清单（本文的红线出处）
├── configs/smoke.yaml                  demo 用配置档
├── trace/ETH_cleaned.csv               真实主网交易数据（328 MB）
├── brokerlab/                          共享库（全部机制与底座）
│   ├── config.py  chain.py  identity.py  tx.py  ledger.py   ← 底座层
│   ├── tdr_policy.py  tdr_engine.py  tdr_hook.py        ← TDR 层（M2/M4）
│   ├── brokerchain.py  real_data.py  matching.py      ← 机制层
│   ├── broker_engine.py  procmon.py  plotting.py      ← 引擎与观测层
│   └── cli.py  __main__.py  __init__.py               ← 入口层
├── experiments/                        一实验一包
│   ├── exp001_relay_vs_broker/         机制对比（阻塞式）
│   ├── exp002_broker_drain_no_tdr/     资金耗尽基线（阻塞式）
│   ├── exp004_b2e_revenue/             B2E 手续费对照
│   ├── exp005_broker_substrate_benchmark/  执行基底基准（流水线）
│   ├── exp006_rate_invariance/         注入速率扫描（流水线+代铸 coordinator）
│   ├── exp007_dynamic_routing/         动态路由（路由 coordinator，M3 验收）
│   └── exp003_tdr_on_off/              TDR 开/关对照（M2/M4 验收，真实再平衡交易）
└── tests/                              69 项纯函数测试（不需要 anvil）
```

（TDR 三模块落点见 §1.6/1.6b/1.6c；exp003 结果与分析见该包 README 与 EXPERIMENT_DESIGN §7.8。）

### 0.2 谁依赖谁

```
config.py ──→ chain.py ──→ tx.py ──→ brokerchain.py ──→ broker_engine.py
                 ↑           ↑             ↑                  ↑
            identity.py ────┘        real_data.py        matching.py
            procmon.py（独立观测）    plotting.py（独立画图）
                     cli.py（demo 编排，阻塞式世界的总装）
                     experiments/*/run.py（各实验编排，只进不出）
```

读法与依赖同向：底座→机制→引擎→入口→实验。任何文件只用它左边的东西。

### 0.3 一笔数据的一生（以 exp006 为例）

```
trace CSV 行 ──extract()──→ RealCtx（映射出 src/dst/账户/金额）
→ 巨鲸组过滤 + LPT 装箱 ──→ 50 份"箱"（每箱 = 一个 broker 的任务清单）
→ EngineSpec（可 pickle 的任务包）──→ 50 个引擎子进程
   引擎内：pump() 按到达门放行 → send_transfer 签名发送 → poll() 查回执
   dst 钱不够 → relay 兜底：burn 段自签 + 请求 coordinator 代铸 mint 段
→ 每笔一行度量（ctx_rows.csv）→ verify_*() 守恒与判据 → summary.json → 图
```

### 0.4 推荐阅读路线（约 3 小时）

| 轮 | 文件 | 目标 | 时长 |
|---|---|---|---|
| 一 | config → chain → identity → tx → ledger → procmon | 底座：参数、链、账户、交易、账本 | 65 min |
| 二 | brokerchain → real_data → matching | 机制：两路径语义与数据来源 | 40 min |
| 三 | broker_engine → procmon → cli | 引擎：流水线 + 观测 + 总装 | 50 min |
| 四 | exp001 → exp002 → exp004 → exp005 → exp006 → exp007 → exp003 | 实验：判据落在哪 | 按兴趣 |
| 五 | tests/ + 附录红线清单 | 复核 | 20 min |

第二轮结束时，你就具备审核全部实验脚本的能力。第三轮开始才有"新代码"。

---

## 1 底座层

### 1.1 brokerlab/config.py —— 参数的唯一户口

**职责**：YAML → 只读 dataclass。它定义了全部实验可调参数，并强制五条规则（文件头注释即规则原文）。

**按这个顺序读**：
1. 六个 Section 类（ChainCfg/ScaleCfg/BrokerCfg/TrafficCfg/TDRCfg/B2ECfg/OutputCfg）：每个字段带单位后缀（`_wei`、`_eth`、`_s`、`_blocks`）。
2. `RunCfg`：聚合根。注意两个只读属性把 ETH 换算成 wei。
3. `load_config()`：读 YAML → 逐段构造 → 处理 `traffic.real_csv_path` 的项目根锚定 → validate。
4. `apply_overrides()`：`--set 段.字段=值` 的覆盖路径。exp 段是自由字典（`_coerce_guess` 猜类型）。
5. `validate()`：所有结构性约束都在这——分片数、端口空间、账户索引三段互斥、TDR/B2E 参数域。
6. `b2e_fees()`：B2E 费用的定点整数拆分（β 放大 9 位小数再整除，杜绝浮点误差）。

**审核要点**：
- 任何参数都该能在这里找到户口；实验脚本里搜"默认值"三个字应该搜不到（参数纪律）。
- 索引三段约束 `coordinator < broker_base < user_base` 是 nonce 不撞车的第一道闸。
- `setBalance` 红线在本文件没有逻辑，但 `relay_mint_budget_eth` 的注释解释了它与流动性的区别。

### 1.2 brokerlab/chain.py —— 分片从哪来

**职责**：管理 anvil 子进程集群（启停、端口、日志），并提供每分片一个的 Web3 连接。

**按这个顺序读**：
1. `resolve_anvil_bin()`：可执行文件查找链（配置→~/.foundry/bin→PATH）。
2. `_port_free()`：启动前 bind 探测。注释解释了 SO_REUSEADDR 为什么必须有（TIME_WAIT 假阳性踩坑记录）。
3. `AnvilCluster.start()`：核心。逐端口探测→逐分片 spawn→等 1 秒查崩溃。节点参数块注释解释了每个开关干什么（chainId 防重放、共享助记词造出子账户、零费用保守恒、fifo 降随机、prune 控内存）。
4. `AnvilCluster.stop()`：terminate → 超时 → kill，只动自家 PID。
5. `Connections`：按分片缓存 Web3 句柄；`wait_all_ready()` 是 RPC 就绪门。

**审核要点**：
- 全局 pkill 是审计 F8 的死罪，本文件只操作 `self._procs`。
- 链状态全在内存、日志在运行目录——换机器不需要清理任何东西。

### 1.3 brokerlab/identity.py —— 账户与 nonce

**职责**：从一条助记词确定性派生账户（缓存）；按 (地址, 分片) 维护本地 nonce 计数器。

**读点**：
- `UserManager`：路径 `m/44'/60'/0'/0/{index}`。索引段（1=coordinator、100+ = broker、200+ = 用户）由 config.validate 保证互斥。
- `NonceManager.next()`：只增不回头。`sync()` 只在 setup 被调。**为什么敢本地计数不查链**：注释里那条"一个 (账户,分片) 恰好一个写入者"的所有权不变量——它是全部并发设计的根。

**审核要点**：这个类没有锁，也不该有。任何"给 NonceManager 加锁"的改动都意味着所有权被破坏了。

### 1.4 brokerlab/tx.py —— 一笔交易的七个动作

**职责**：对 anvil 集群做真实签名转账；采集实验度量（RLP 字节、回执字节、提交块高）。

**按这个顺序读**：
1. `send_transfer()`：组 legacy 交易→本地 nonce→签名→裸发送。docstring 解释 gasPrice=0 与 chainId 绑定分片两个设计点。它顺手记下 `submit_block`/`tx_bytes`。
2. `probe_receipt()`：单探针，查一次不睡眠。**三态语义**的出处：None=未入块，0=revert，1=成功。
3. `wait_receipt()`：阻塞轮询版（0.2 s 一次），内部复用 probe。
4. `get_balance()` / `block_number()` / `sync_nonces()`：读侧。
5. `set_balance_setup()`：**全文件最需要盯的名字**。函数名里的 `_setup` 是审查标记，docstring 写明红线。

**审核要点**：`_tx_bytes`、`_receipt_bytes`、`_submit_block` 三个字典是"实测不造数"纪律的落点——relay 的 2 KB/笔通信账单就是从它们算出来的。

### 1.5 brokerlab/ledger.py —— 三本账防火墙（exp007 起用）

**职责**：把 broker 的钱分账管理：`confirmed`（链上已确认）与 `reserved`（已承诺未落定）。
服务判定只看一个数：`available = confirmed − reserved`。
**读点**：四个方法 `reserve / release / confirm_debit / confirm_credit`，每个都带断言——
预留不超可用、释放不超已预留、确认付出必须有对应预留。负数一律抛 `LedgerError`。
**为什么不记 incoming**：文件头注释就是答案（审计 F5：gen-3 把未到账当可用，酿成双花）。
**单测**：test_ledger.py 的 F5 回归用例复现了那个历史事故的场景。

### 1.6 brokerlab/tdr_policy.py —— 再平衡的"大脑"（M2，逐语义镜像 Go）

纯函数三层，各自镜像 Go 的一个文件（文件头注释给对照表）：
- `DemandWindow`：W 块滑窗需求。**observe 允许回填**：CTX 提交后按真实块高
  记入，内容等价于"路由时刻记录"（审计 F3 修复：被 relay 的请求照样计需求）。
- `compute_target`：τ 向量。Phase A 比例整数分、余数归最大 λ；Phase B q_min 地板
  抬升 + 超额按比例回收 + 末位吸收残差；最后 reconcile 保证 Στ=ρ。
  全部 BPS/定点整数，逐行与 Go `applyConstraints` 对齐。
- `compute_transfers`：死区只决定动不动；一旦触发，T 覆盖全部正负 gap，
  降序双指针 min(头,头)，|T|=max(|盈余|,|短缺|)（Lemma 1）。
Go 交叉样例（tdr_test.go 移植）在 test_tdr_policy.py，六个全过。

### 1.6b brokerlab/tdr_engine.py —— TDR 状态机

一个 broker 的决策与事件生命周期：IDLE →(触发&χ满足&无活跃)→ EVENT_ACTIVE →
各笔 FIRST_SUBMITTED → FIRST_CONFIRMED → SECOND_REQUESTED → DONE|FAILED →
EVENT_CLOSED → IDLE。三条纪律（互斥 / χ 块间隔 / 超时记 lost_in_transit）在此落地。
**第二段超时的归属（设计决定，踩坑后修正）**：mint 段的回执必达（coordinator 有
relay_timeout 兜底显式回 0），状态机不抢先判其超时——否则与"后到的成功回执"双记。

### 1.6c brokerlab/tdr_hook.py —— 挂进引擎

`TdrDynamicEngine(DynamicBrokerEngine)`：每块 tick 先喂需求（settle 回看）、
再推进已提交段（confirm/ack/timeout）、最后决定是否开新事件。两段执行：
第一段 broker@src→BURN 自签（本地 nonce，一箱一主不变）；第二段 CreditRequest
{receiver_idx = broker 自己的账户} → 与 relay 共用代铸通道，coordinator 零改动。
终局以状态机为权威 + `_await_ack` 空（每笔 mint 都必须拿到回执才收尾）。

### 1.7 brokerlab/broker_engine.py 的动态子类（exp007）

`DynamicBrokerEngine` 与父类只差三件事（类 docstring 即合同）：
① 批次来源 = `ctx_queue`（coordinator 逐块投递，END 哨兵收尾）；
② dst 不够不停表，改判 relay（burn 用 coordinator 预分配的 nonce，mint 走代铸队列）；
③ 每段落定向 `rep_q` 回报一行摘要（coordinator 据此释放 sender 窗口）。
单写者约定从"一 (账户,分片) 一签"变为"一 (账户,分片,nonce) 一签"——docstring 写明。

### 1.8 brokerlab/procmon.py —— 资源监视器

**职责**：每秒求和被跟踪进程的 RSS（读 /proc）。越限时置 `tripped`，实验主循环看到就中止。

**读点**：文件头解释了为什么不能用 `getrusage`（它只报单个子进程峰值，不能求树和）。exp005 里有一份内联旧版（`RSSMonitor`），exp006 起用本文件的 `RSSWatchdog`。

---

## 2 机制层

### 2.1 brokerlab/brokerchain.py —— 两种机制的全部语义（必读，最核心）

**职责**：把"一笔 CTX 怎么被服务"写成可执行定义。模块 docstring 本身就是设计说明书——**先把它读完再读代码**。

**数据结构（读代码前先背下这三层）**：
- `CTX`：需求。字段注释逐个写明含义（v 是 wei）。
- `HalfResult`：一段链上交易的记录（哈希、状态、落块、时间戳、字节数）。`ok` 和 `hops` 两个属性。
- `CtxResult`：一笔 CTX 的完整记录 = Θ1 + （可选的 Θ1b 烧币段）+ Θ2 + 费用字段。

**执行路径**：
1. `execute(ctx, route, broker_idx, fee_broker_wei, fee_burn_wei)`：参数分派。两个费用参数默认 0 ⇒ 与 B2E 之前行为完全一致（回归不变式，写在 docstring）。
2. `_run_half()`：签→发→等回执，并沿途采集（`submit_block`、`tx_bytes`、`confirm_ts`、`receipt_bytes`）。
3. `_execute_broker()`：**两段式承诺**——Θ2 只有在 Θ1（及 Θ1b）确认成功后才发出；Θ1 失败就整体早退，broker 目的侧资金分毫未动。
4. `_execute_relay()`：burn-and-mint。Θ1 把 v+全额 F 烧进 BURN_ADDRESS；Θ2 由 coordinator 账户真实转账代表铸币。**为什么 coordinator 的钱不是流动性**：模块 docstring 讲铸造预算与 Σ销毁恒等的语义（审计 F2 的修复核心）。

**审核要点**：
- 本文件没有任何"挑哪个 broker"的逻辑——机制与政策分离，匹配在 matching.py。
- 没有任何 `set_balance` 调用——红线在机制层成立。
- broker_address 的注释把"一个地址 = |𝕊| 个子账户"的论文口径钉在本文件（𝕊 是全体分片的集合）。

### 2.2 brokerlab/real_data.py —— 真实数据 → CTX

**职责**：流式读 328 MB 主网 CSV，过滤、映射出 `RealCtx` 列表。

**读点**：
1. 文件头两条映射公式（分片号 = 地址转整数 mod 分片数；账户索引 = 基数 + mod 用户数）。**规则与旧三代逐字节一致**——历史可比性的根。
2. `extract()` 的过滤链：排除合约与出错行 → 金额区间 [0.01, 10] ETH（注释解释 cap 挡巨鲸的理由）→ 丢弃同片与账户碰撞行。

**审核要点**：函数里 `limit` 语义是"产出行数上限"，不是"扫描行数"。exp005/006 的巨鲸组过滤在实验侧做，本文件保持纯净。

### 2.3 brokerlab/matching.py —— 谁服务这笔 CTX

**职责**：static_broker 策略，纯函数两步：eligible = 目的余额 ≥ v 的 broker；带种子 RNG 均匀选一个。找不到返回 None。

**审核要点**：docstring 写明了两条纪律——为什么必须均匀（"挑余额最大"等于把半个 TDR 偷装进基线）；为什么可插拔（日后消融只换本文件）。

---

## 3 引擎与入口层

### 3.1 brokerlab/broker_engine.py —— 流水线引擎（新代码的重心）

**职责**：单 broker 的流水线执行体。它是 M3 broker 块循环的前身。**模块 docstring 是合同**：它明写了本引擎"不是"什么（无队列摄取、无账本三本账、无 TDR）。

**先读两个约束**（docstring 前四行）：
1. Θ2 只等**自己的** Θ1（逐笔两阶段承诺）。
2. 发 Θ1 前 dst 侧预留：`available = mirror − committed`（mirror 是内存镜像账，committed 是在途承诺）。
下一笔的 Θ1 不等上一笔——这是它与 BrokerChain.execute（阻塞式）的全部差别。

**纯函数组（无 I/O，有单测）**：
1. `group_key()`：nonce 冲突域 = (sender_idx, src_shard)。注释解释为什么不能只按 sender 分。
2. `assign_sender_groups()`：LPT 装箱（大组先装进最空的箱）。注释写明均衡与可交换性动机。
3. `funding_plan()`：按需求算垫资（"充足即止"纪律）。

**引擎类（状态机）**：
- 每笔在途 CTX 有 stage：1=等 Θ1 回执，2=等 Θ2 回执，4=等 coordinator 代铸回执（relay 专用）。
- `tick_once()` = `_pump()` + `_poll()`，全程不睡眠（协作式）。
- `_pump()`：先查到达门（`release()` 返回全局已释放笔数——**块驱动**，exp006 的灵魂），再查 dst 预留；不够时：配了 credit 队列 ⇒ `_submit_relay()` 兜底，没配 ⇒ 队首停表（exp005 语义）。
- `_poll()`：对每段做节流探针；Θ1 确认→立刻发 Θ2；stage 4 从 `_acks` 字典按 ref 匹配代铸回执（防错位，注释说明）。
- `run_blocking()`：线程/进程模式入口（等闸门→自旋 tick）。
- `envelope()`：结果信封（全 plain 类型 ⇒ 可 pickle 过 mp.Queue）。
- `build_services()` / `engine_from_payload()`：从 plain dict 重建全套服务——spawn 进程里"一人一份连接"的出处。

**审核要点**：
- 单写者不变量：引擎只签"自己箱内 sender 组的 Θ1"和"自己的 Θ2"。assign_sender_groups 的组不可拆保证前者；一箱一主保证后者。
- 三个计数器是三种停表原因，别混淆：`arrival_gated`（到达没到）、`reserve_blocked`（钱不够且无兜底）、`relay_fallback`（钱不够、改走 burn-and-mint）。

### 3.2 brokerlab/cli.py —— M1 demo（最小完整运行）

**职责**：doctor 体检 + demo 总装。demo 是全平台语义的活文档。

**按 cmd_demo 的六个编号段读**（注释分节极清楚）：
1. 起链 → 2. 选 2 笔真实 CTX（互不共账户）→ 3. setup 充值（**全文件唯一合法 setBalance 窗口**，每笔充值都有注释说为什么：broker 100/片、coordinator 铸造预算、sender +1 ETH 缓冲、receiver 留 0 作接收证明）→ 4. 双路径执行 → 5. 九项守恒核对（8 项逐点差分 + Σ铸≡Σ烧）→ 6. report.json。

**为什么值得先跑它**：`python -m brokerlab demo --config configs/smoke.yaml` 两分钟跑完，对着 report.json 读代码最快。

### 3.3 brokerlab/plotting.py —— 图样

配色与手稿一致（蓝 broker、橙 relay、红数据量），后端强制 Agg 免显示环境。

---

## 4 实验包（每个都是 README → config.yaml → run.py 三步读）

**通用骨架**（五个包都一样）：`exp_param()` 缺键即报错；起链→充值（setup 唯一窗口）→执行→对账→写产物；退出码 = 判据。差异只在判据与容器。

### 4.1 exp001_relay_vs_broker —— 两机制开销量子（阻塞式）
- 设计：同一行 trace **双路径各跑一次**，逐对交替先后（消顺序效应）。
- 判据（`run()` 尾部）：H1 两路径 e2e 相对差 < 20%；H2 relay 字节 > broker 字节；H3 链上段数相等。
- 读点：`xmit` 一行是全部通信口径——relay = Θ1 RLP + 回执 JSON（实测），broker = 声明常数 100 B。
- 结果在 `out/20260904_024203/`：20× 通信差、足迹对称。

### 4.2 exp002_broker_drain_no_tdr —— 资金耗尽复现（阻塞式）
- 设计：多数方向流 150 笔，两档垫资（100/300）各起一条新链，**同一 seed ⇒ 逐笔可比**。
- 判据（`main()` 中 C1-C4）：dst 终值 <10% 且总漂移 ≈0；relay 份额沿四分位递增；每笔字节上升；加充值只推迟。
- 读点：`quartile_shares()` 按执行序切（块号类指标跨跑抖动 ±40%，故弃用——README 有账）。

### 4.3 exp004_b2e_revenue —— 手续费第一次在链上立账
- 设计：off/on 两方案同一股流。on：βF 搭 Θ1a 进 broker，(1−β)F 由额外一段 Θ1b 烧掉；relay 全额 F 随 v 进 BURN。
- 判据（`verify()`）：I1 全局净和 0；I2 Δbroker ≡ ΣβF（含逐 broker 的 I2b）；I3 ΔBURN ≡ 台账。
- 读点：机制层没政策——`execute()` 的 fee 参数由 run.py 从 `b2e_fees()` 算好传入。

### 4.4 exp005_broker_substrate_benchmark —— 三种容器的赛跑
- 设计：同一引擎、同一静态指派负载，三种容器各跑：serial（单进程轮转 50 引擎）/ threads（50 线程）/ processes（50 子进程）。
- 关键设施：`build_workload()` 的巨鲸过滤与 LPT；`RSSMonitor`；`_mp_worker`（spawn 入口，错误经队列回传）。
- 判据：G1-G3 全容器守恒；G4 流水线 ≥5× exp001 阻塞口径（`load_anchor()` 读 exp001 产物做外部锚）；H-B1 加速比。
- 结果：线程 0.82×（GIL 锁单核），进程 3.8×。退出码 1 是诚实记录（线程拖负联合判据）。

### 4.5 exp006_rate_invariance —— 注入速率会不会改变结论
- 设计：50 broker × 4 分片 × 10000 笔 + **单 coordinator 进程**。到达由父进程监视器按块释放：`released = (min分片块高 − h0) × ctx_per_block`。
- 结构：`_engine_worker`（50 进程，release 回调 + credit 队列）、`_coordinator_worker`（代铸 mint 段，回执按 ref 路由）、`run_rate()`（一速率档的全流程）、`verify_rate()`（守恒 + 决策指纹 digest）。
- 判据：H-R1 决策指纹跨全部速率档一致（route 由余额在 pump 时刻定死 ⇒ 与墙钟无关）；H-R2 各档净和 0；吞吐曲线找平台。
- 结果：25→300 CTX/块 四档指纹相同；上限 ≈120 CTX/s（图 (a) 平台）；代价是 p95 延迟不是吞吐。

### 4.6 exp007_dynamic_routing —— 路由实时化（M3 验收）
- 设计：路由不再启动期装箱，改由 **coordinator 进程实时指派**。同流静态臂做同会话参照。
- 关键设施：`_coordinator_worker` 五步块循环（释放→估计表刷新→派发→代铸→回收）；
  每笔 nonce 由 coordinator 串行预分配；`DynamicBrokerEngine` 本地终审 + BrokerLedger。
- 判据：declared 两连跑一致（回归门）；G1-G4 两臂守恒；H-D1 吞吐 ≥0.8× 静态；H-D2 陈旧证据。
- 结果：改判 ≈27% 下守恒零损；动态 = 静态 0.44–0.62×（瓶颈=coordinator 单环，详见 exp003 修正）。

### 4.7 exp003_tdr_on_off —— TDR 开/关对照（M2/M4 验收）
- 设计：同一动态流，两臂各起全新链。tdr 臂每引擎挂 `TdrDynamicEngine`（burn+代铸两半段搬平）。
- 关键设施：`TdrAgent` 事件状态机（§1.6b）经 `_tdr_plan/_submit_tdr_first/_tdr_poll` 跑成真链交易
  （§1.6c）；需求按 Θ1 提交块高回看（F3 修复）；`tdr_close_tail_s` 单列收尾时长（吞吐口径修正）。
- 判据：两臂 gates（G1-G4 + burn≡mint）全过；H-T1 改判下降；H-T2 洪流下守恒；H-T3 代价三件套。
- 结果：改判 27.1%→0.5%、成交 7286→9947；773 事件/2294 笔/104764 ETH 搬运零丢失；
  CTX 吞吐 0.912×、收尾时长 22.3 s、e2e p95 1.026×。归因：吞吐瓶颈是 coordinator 单环，非 relay。

---

## 5 测试层（tests/，69 项，全部不需要 anvil）

| 文件 | 锁住什么 |
|---|---|
| test_chain.py | 端口探针不被 TIME_WAIT 骗、不被真监听漏 |
| test_config.py | 加载/校验/--set/快照 + 三个实验包配置可加载 |
| test_matching.py | 资格集合、种子可复现、均匀覆盖 |
| test_real_data.py | 映射规则与旧三代逐字节一致、过滤、limit |
| test_broker_engine.py | LPT 不变量（互斥/完备/确定）、pump/poll 状态机、到达门、relay 兜底、铸币超时 |
| test_ledger.py | 可用额公式、预留-释放配对、不许负值、双扣拒绝、F5 场景回归 |
| test_tdr_policy.py | DemandWindow 滑窗、compute_target 地板/余数/reconcile、compute_transfers 两触发模式、Lemma-1 上界、Go 交叉样例 |
| test_tdr_engine.py | 事件状态机全序、互斥、χ 间隔、超时归属（首段判失、末段不回冲）、幂等晚到回报 |
| test_b2e.py | 费用拆分精确到 wei（含浮点陷阱用例）、机制层段序列（无费用逐字节旧路径/有费用三段/relay 全烧/烧币失败不放行 Θ2） |

读法建议：engine 与 b2e 两组测试本身是最好的"行为规格说明书"，注释里写了每条断言的动机。

---

## 6 红线清单（审核代码时逐条对）

| # | 红线 | 落点 |
|---|---|---|
| 1 | setBalance 只许出现在 setup 充值 | `tx.set_balance_setup` 命名即标记；全仓搜索非 setup 调用 = 0 |
| 2 | 负载/策略时机禁止墙钟 | exp006/007/003 的 released=块龄×rate（块高驱动）；TDR 的 χ/deadline/滑窗以块计、h_est 单调取两分片 min（tdr_hook）；time.sleep 只用于轮询间隔与展示 |
| 3 | 一切度量实测；模型常数必须显式声明 | 唯一常数 match_ctrl_bytes=100 B（exp001 config 注释自证方向安全） |
| 4 | 一个 (账户,分片[,nonce]) 恰好一个签名者 | 静态：LPT 组不拆；动态：coordinator 串行分 nonce + sender 窗口（exp007） |
| 5 | 参数唯一来源 = config.yaml | 各 run.py 的 `exp_param()` 缺键即退出 |
| 6 | 只终止自家子进程 | chain.py stop；pids.json |
| 7 | 失败与缺失可区分 | 回执三态 None/0/1；早退不放行下一段 |

## 7 符号速查（代码内出现的记号）

| 符号 | 含义 |
|---|---|
| v | 一笔 CTX 的转账金额（wei） |
| S / R | 发送者 / 接收者账户 |
| B_s / B_d | broker 在源 / 目的分片的子账户 |
| C | coordinator 账户（代表目的委员会铸币） |
| BURN | 死地址 0x…dEaD |
| src / dst | 源 / 目的分片号 |
| mirror / committed | 引擎内存镜像余额 / 在途承诺 |
| arrival_pos / released | 全局到达序 / 已释放笔数 |
| ctx_per_block | 每逻辑块释放几笔（exp006 自变量） |
| F / β | B2E 费用基数 / broker 抽取比例 |
| η | 服务率 = served ÷ 总数（实验脚本计算） |
| hops | 一段等入块的块数（confirm 块高 − 提交时块高） |

## 8 跑一遍再读（强烈建议）

```bash
cd exp_anvil_broker_qwen
python -m brokerlab doctor --config configs/smoke.yaml
python -m brokerlab demo --config configs/smoke.yaml     # 2 分钟，读 report.json
pytest tests/ -q                                          # 1 秒，读 69 条断言注释
cd experiments/exp001_relay_vs_broker && python run.py    # 2 分钟，对照 §4.1
```

demo 与 exp001 跑通后，本文所有"为什么"都变成了你能亲眼指认的事实。
