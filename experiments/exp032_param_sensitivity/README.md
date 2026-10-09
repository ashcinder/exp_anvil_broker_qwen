# exp032 · 10W 口径参数敏感性（TDR 定稿调度）

**测什么**：把 exp008 的定稿评估口径从 **4W 笔/轮拉到 10W 笔/轮**，在这个规模上做
TDR 调度参数的**敏感性扫描**（ε / q_min / hl，每参数 20 点位 × 3 场）。

**为什么新开一包**：exp008 保留为 4W 历史档（不改），exp032 是 10W 新口径。
管线复用 exp003（与 exp008/exp010 同一份代码、同一套 gates），本包只负责编排与聚合。

**为什么先定标速率**：文档里的 `rate=120` 来自**另一台机器**（exp003 档案 §8：6 核机
上限 80-85 CTX/s）。本机 20 核 / 31 GB，按计划必须实测——`calibrate_rate.py` 就是这件事。

## 口径（用户 2026-09-28 裁定）

| 项 | exp008 (4W) | exp032 (10W) | 说明 |
|---|---|---|---|
| `exp.ctx_per_broker` | 800 | **2000** | 50 broker × 2000 = 100000 笔 |
| `exp.pool_scan` | 200000 | **500000** | 实测：scan=200000 只凑出 71935 笔；500000 → 可用 186460 |
| `exp.max_backlog` | 20000 | **50000** | 随 N 同比例（N 的一半） |
| `exp.drain_tail_s` | 420 | **900** | 让 coordinator deadline 不成为人为约束 |
| `exp.balances_eth` | 150 | **150** | **不随 N 放大** ⇒ 紧张度 = exp008 的 2.5 倍 |
| 场数 | 2 | **3** | 单场 relay 噪声 2.5×，论文引跨场中位 |
| `exp.rate` | 120 | **定标结果** | `calibrate_rate.py` 实测后回填 |

**效度声明**：垫资不随 N 放大，10W 档比 exp008 更接近抽干极限，
relay 与 TDR 搬量的量级**不能**与 exp008/exp010 的历史数字直接比；
两组各自内部可比，跨组只比趋势与相对排序。

## 速率定标（`calibrate_rate.py`）——本包的第一步

```bash
python calibrate_rate.py --rates 120,200,300,400      # 升序串行，约 1.5-2 机时
```

- 定标臂用**最重的生产臂** `topup@150@@0.95@@20`（带 coordinator 代铸负载），
  比 exp003 §8 用 plain 定标更保守——它过得去的速率对所有臂都安全。
- 规模 = 生产规模（16 分片 × 50 broker × N=100000），否则尺度效应测不到。
- **安全判据（四条全过）**：`aborted is None` ∧ gates 全 True ∧ burn≡mint ∧
  `backlog_peak < 0.6 × max_backlog` ∧ RSS < `rss_cap_mb`。
- 产物 `out/calib_<ts>/rate_report.json`（含 `max_safe_rate` + 每档明细）。
- **必须串行**：档案 §8 的教训——两个 run 并行会互相拖慢，导致 deadline 假超时。

**本机实测结果（2026-09-28，`out/calib_20260928_112412/rate_report.json`）**
规模 16 分片 × 50 broker × N=100000，臂 `topup@150@@0.95@@20`，backlog cap=50000：

| rate | 墙钟 | 吞吐 CTX/s | backlog 峰值 | 占 cap | e2e p95 | relay | gates | 判定 |
|---|---|---|---|---|---|---|---|---|
| **120** | 24.8 min | 67.2 | 29552 | 59% | 2.10 s | 55 | 全 True | ✅ 采用 |
| 150 | — | 75.1 | 34588 | 69% | — | — | 全 True | 余量不足 |
| 200 | 19.4 min | 85.8 | 41902 | 84% | 2.10 s | 8 | 全 True | 余量不足 |
| 250 | — | 93.6 | 47672 | 95% | — | — | 全 True | 余量不足 |
| 300 | 5.1 min | — | 50071 | 100% | — | — | G2/G4 FAIL | ❌ abort |
| 400 | 3.2 min | — | 50171 | 100% | — | — | G2/G4 FAIL | ❌ abort |

判读：① 本机持续服务上限 ≈ 86–94 CTX/s，注入速率再高只堆队列；
② **backlog（已释放未派发）随 rate 单调上涨，与注入速率一一对应**，撞 50000 即整场作废；
③ 单笔 e2e 延迟与速率无关（四档均 ≈1.9 s 均值 / 2.10 s p95）——积压只在队列上，不进 CTX 延迟；
④ relay 反随 rate 下降（55→8→5）：高注入下 dst 更早被 TDR 补血。
→ **正式档取 rate=120**（用户 2026-09-28 裁定）。

## 实验本体（`run.py`）——五方案同场 × 3 场

```bash
python run.py                                  # 主表（默认 3 场）
python run.py --set exp.exp032_sessions=1      # 单场快验
```

五方案（token 语法 `name@fund@cap@eps@chi@hl@qmin`）：
`plain@150` / `valve@150@1.3` / `tdr@150@@0.1`（论文 proportional）/
`topup@150@@0.1`（硬窗）/ `topup@150@@0.95@@20`（**定稿** topup+EWMA）。

**判据**（`report.json → verdicts`）：
- **V1** 每场每方案 gates（G1-G4 + burn≡mint）全 True。
- **V2** 定稿方案 relay 跨场中位 ≤ `exp032_relay_median_max`
  （10W + 150 垫资的 relay 量级与 exp008 的 50 不可比，门限待首场实测后回填）。
- **V3** 定稿方案搬量中位落在物理下限 ±10%（下限 = 164k ETH @4W 按 ctx 线性折算）。

## 参数敏感性网格（`sweep.py`）——每参数 20 点位 × 3 场

```bash
python sweep.py --dry-run              # 看计划
python sweep.py --only E1_eps_grid     # 单组补跑/重跑
python sweep.py                        # 全网格（180 臂跑，串行）
```

| 批次 | 扫描 | 点位 | 固定其余两维 |
|---|---|---|---|
| `E1_eps_grid` | ε = 0.05→1.00 步长 0.05 | 20（含 1.0 失效悬崖） | q=0.10, hl=20 |
| `Q1_qmin_grid` | q_min = 0.00→0.19 步长 0.01 | 20（含 0 无保险对照） | ε=0.95, hl=20 |
| `H1_hl_grid` | hl = 2,3,4,5,6,8,10,12,14,16,18,20,24,28,32,40,48,56,64,80 | 20 | ε=0.95, q=0.10 |

- seed=42 / route_seed=7 **全批次冻结**：本搜索度量的是同流下参数与时序的效应。
- 产物：`out/<ts>/grid_results.csv`（逐场追加，中途可查）、
  `out/<ts>/grid_report.json`（批次→方案→跨场中位/极值/散点）、
  `out/<ts>/<批次id>__r<场次>/<exp003 时间戳>/<方案token>/`（每臂完整现场）。
- 任何 gates False / 崩溃都**保留现场**，记 aborted 行不掩盖。

## TDR 结果的块号（本次新增）

`brokerlab/tdr_hook.py` 原来只记 `(src, dst, amount)`，看不出 TDR 在哪个块执行。
现在每个已完成搬运记 `(src, dst, amount, burn_block, mint_block)`，
`tdr_moves.csv` 表头相应变为
`broker_idx, src_shard, dst_shard, amount_wei, burn_block, mint_block, state`。
burn_block 是首段（broker→BURN）回执块、mint_block 是二段（coordinator 代铸）回执块；
两者此前都已经取到、只是被丢弃。对齐检查：`mint_block ≥ burn_block ≥ 事件起始块`。

## 运行环境

conda `scienv`（`/opt/conda/envs/scienv/bin/python`）。体检：
`python -m brokerlab doctor --config experiments/exp032_param_sensitivity/config.yaml`。
