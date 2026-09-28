# exp008 · 定稿调度方案正式评估（论文数据生产）

**测什么**：把 exp003 探索定稿的调度方案固化为可一键复现的生产实验，
与三个对照方案同场多轮对比，产出论文图表数据。

**定稿方案**：topup + EWMA（只补缺口 + 需求指数平滑）。
参数：ε=0.95、半衰期 hl=20 块、q_min=0.10、χ=10 块。
全本地执行：只用链上数据与 broker 自有资金，无外部提示。
设计全文：[../exp003_tdr_on_off/DESIGN_topup_ewma_CN.md](../exp003_tdr_on_off/DESIGN_topup_ewma_CN.md)。
探索史（含全部失败）：[../exp003_tdr_on_off/EXPLORATION_log_CN.md](../exp003_tdr_on_off/EXPLORATION_log_CN.md)。

**同场五方案**（同一负载、同一 seed、只换处理）：
- `plain@150`：不搬钱（基线，relay 最多）。
- `valve@150@1.3`：傻瓜水位阀（消融对照，无需求预测）。
- `tdr@150@@0.1`：原论文式 proportional（需求比例 + 全量重排）。
- `topup@150@@0.1`：topup 硬窗口版（只补缺口，无平滑）。
- `topup@150@@0.95@@20`：**定稿方案**（topup + EWMA）。

**负载**：16 分片 × 每分片 150 ETH × 40000 笔真实 trace CTX。
dst8 集中约 75% 需求——定稿方案的优势场景（写进论文限制声明）。
注入 rate=120（本机最快安全档，exp003 档案 §8 定标）。

**判据**（`report.json → verdicts`）：
- V1 每场每方案 gates（G1-G4 + burn≡mint）全 True。
- V2 定稿方案 relay 跨场中位 ≤ 50（0.125%；历史样本 5-88）。
- V3 定稿方案搬量中位落在物理下限 ±10%
  （下限 = 建仓 + 单向净排水 ≈ 164k ETH @40000 笔，档案 §19）。

**为什么多场**：单场 relay 噪声实测 2.5 倍（档案 §9/§16）。
论文引用跨场中位与区间。默认 sessions=2，可加。

**运行**：
```bash
python -m brokerlab doctor --config experiments/exp008_tdr_schedule_final/config.yaml
python run.py                                        # 正式生产（默认 2 场，约 2 小时）
python run.py --set exp.exp008_sessions=1            # 单场快验
python run.py --set exp.ctx_per_broker=4 --set chain.num_shards=4 \
  --set exp.exp008_sessions=1                        # 冒烟
```

**产物**：`out/<ts>/session_k/…`（每场完整 exp003 管线产物）；
`out/<ts>/report.json`（跨场聚合：每方案 relay/次数/搬量/段占比的中位与极值）。

**消融图（比原稿多出的一张）**：`plot_ablation.py` →
`figs/exp008_ablation.png`。阶梯式消融六档（同负载逐级加一个组件）：
不搬 → 搬运(均匀目标) → 需求目标(全量重排) → 只补缺口 → 深死区 → 需求平滑(定稿)。
面板 (a)relay (b)搬运次数 (c)搬量 均对数轴；(d)死区 ε 灵敏度含 ε→1 悬崖
（矩阵档 EWMA 口径曲线）。依赖一次消融补跑：
```bash
python run.py --set exp.exp008_sessions=1 \
  --set exp.arms="topup@150@@0.95,topup@150@@0.1@@20,topup@150@@0.95@@20"
python plot_ablation.py
```


**效度声明**：
① 复用 exp003 管线，判据与 M4 验收同源，无新机制代码。
② 优势场景依赖 dst 高度集中；低集中度场景 valve 更省（档案 §3）。
③ ε=0.95 距失效悬崖（1.0）0.05，鲁棒性依赖需求不换向（未测）。
④ 吞吐只作次级证据：rate120 已近机器平台，与方案无关。

**最近结果**：`out/20260910_033927/`（2026-09-10，sessions=2，
每方案 2 样本的跨场统计；V1/V2/V3 全 True）。

| 方案 | relay 中位 [min..max] | 搬运次数 | 搬量 ETH | TDR 段占比 |
|---|---|---|---|---|
| plain | 24848 [24835..24860] | 0 | 0 | 0 |
| valve | 20863 [20608..21117] | 273 | 13045 | 0.7% |
| proportional | 110 [107..112] | 22642 | 813408 | 57% |
| topup 硬窗 | 72 [61..83] | 17206 | 807759 | 43% |
| **定稿 topup EWMA** | **20 [8..32]** | **1227** | **163674** | **3.1%** |

定稿方案 vs proportional：relay 低 5.5 倍、次数省 95%、搬量省 80%。
搬量 163.7k 与物理下限 164k 几乎重合（V3）。
合并 exp003 既有 ε0.95 样本共 5 个：5/8/15/25/32，中位 15。

