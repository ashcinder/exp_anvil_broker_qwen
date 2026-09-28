# 实验 10：TDR / EWMA 暴力参数搜索

实验 10 复用实验 8 的真实 trace、五方案同场比较和实验 3 的链上执行管线，
但把固定配置改造成可恢复、可分批的大范围参数搜索。

## 搜索结构

1. **OAT 单因素扫描**：其他参数固定，只改变一个参数；适合解释因果方向。
2. **二维网格**：搜索 EWMA epsilon×半衰期、epsilon×q_min、
   余额×valve阈值、交易最小值×最大值，以及两种对照策略的
   epsilon×窗口长度。
3. **显式笛卡尔搜索**：对核心策略、流动性、时序三个参数组做暴力组合。
   组合规模很大，因此必须通过 `--stage cartesian --only NAME` 主动启动。

搜索空间在 `search_space.yaml` 中。固定压力基准为：1 ETH/分片、交易金额
0.25～3 ETH、4 区块窗口、EWMA epsilon=0.5、半衰期=4、q_min=0.2、
Valve 倍数=1.3。一次五方案完整基准运行（`out/20260926_085049`）的
Relay、TDR events、TDR transfers 分别是：Plain 162/0/0、
Valve 167/93/149、Proportional 183/60/900、Hard topup 162/67/884、
EWMA 170/59/624。它确认了负载有区分度；单次运行的策略排名不稳定，
候选仍需多个 route seed 复验。

| OAT 参数 | 基准值 | 搜索值 |
|---|---:|---|
| EWMA epsilon | 0.50 | 0.05、0.10、0.20、0.35、0.50、0.65、0.75、0.85、0.95 |
| EWMA 半衰期（区块） | 4 | 1、2、3、4、6、8、12、16 |
| EWMA q_min | 0.20 | 0.20、0.30、0.40、0.50、0.60、0.80 |
| broker 初始余额（ETH/分片） | 1 | 0.5、0.75、1、1.25、1.5、2、3、4、5 |
| 历史需求窗口（区块） | 4 | 2、3、4、5、6、8、10、12 |
| Valve 阈值倍数 | 1.30 | 1.05、1.10、1.20、1.30、1.50、1.75、2.00 |
| Proportional epsilon | 0.10 | 0.05、0.10、0.20、0.35、0.50、0.70、0.85 |
| Hard topup epsilon | 0.20 | 0.05、0.10、0.20、0.35、0.50、0.70、0.85 |
| 交易金额下限（ETH） | 0.25 | 0.05、0.10、0.20、0.25、0.35、0.50、0.75、1.00 |
| 交易金额上限（ETH） | 3 | 1、1.5、2、2.5、3、4、5 |

EWMA 的 `tdr_min_reserve_eth` 固定为 0.25 ETH。旧值 4 ETH 在低余额
试验中会把有效 q_min 强制抬到 1，使需求预测的目标分配退化成平均分配。
当前 1 ETH 基准下有效 q_min 下限为 0.25，因此标称 0.20 的实际效果是
0.25；其余搜索值 0.30～0.80 不受该下限影响。
10～12 区块窗口保留作冷启动对照；1000 笔短批次下它们可能不触发 TDR。

执行器并发、sender window、轮询、超时、chi、安全库存、目标上限和
启动偏移全部固定，不参与本轮参数搜索。

注入速率不参与搜索，所有 trial 固定为 `120 ctx/s`，避免把系统负载变化与
策略参数变化混在一起。

## 运行档位

| profile | 每场交易数 | 默认重复 | 用途 |
|---|---:|---:|---|
| smoke | 200 | 1 | 验证链路和绘图，不作结论 |
| search（默认） | 1,000 | 1 | 暴力搜索；使用完整参数范围 |
| broad | 1,000 | 1 | `search` 的兼容别名 |
| full | 40,000 | 2 | 高成本复核；最终候选仍建议按实验8跑5场 |

当前 `search` 档去重后：OAT 70 个 trial（约 198 个不同 arm 执行），二维
网格 177 个 trial，`--stage all` 共 216 个 trial（约 408 个不同 arm 执行）。
一个 trial 始终包含五个方案；相同配置的 arm 会命中同目录缓存。三组显式
笛卡尔搜索分别为 EWMA 100、负载 81、对照策略 321 个 trial，默认不会运行。

每个 arm 固定注入 `120 CTX/逻辑块`。控制台同时显示两个不应混淆的指标：

- `实际注入`：从启动门打开到最后一笔派发的平均注入速率，目标约 110～120 CTX/s；
- `完成吞吐`：从启动门打开到最后一笔 CTX 落定的端到端吞吐。

1,000 笔短批次的完成吞吐包含流水线填充和两段链上确认收尾，正常情况下会低于
实际注入速率；报告中保留两项数据，不通过修改统计口径掩盖差异。

## 缓存与执行顺序

实验按 arm 的执行代码、配置、trace、route seed 和策略 token 生成内容哈希缓存。
例如只改变 EWMA epsilon 时，plain、valve、proportional 和 hard-window 的结果会
复用，只有 EWMA arm 重新上链执行。缓存只在同一个搜索输出目录中生效；代码或
配置改变会自动换 key。各个真实 arm 仍串行，避免多个 16 分片集群相互争抢资源，
污染吞吐比较。

## 推荐执行顺序

无参数启动默认执行 1,000 笔/arm 的完整 OAT 搜索：

```powershell
python experiments/exp010_tdr_parameter_search/run.py
```

先看规模，不启动链：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile broad --stage oat --dry-run
```

先用固定压力基准运行五个方案各 1,000 笔，并检查是否有可比较的 Relay：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile search --stage baseline
```

冒烟测试：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile smoke --stage oat --only epsilon --max-trials 4
```

单个二维网格的冒烟（3×3）：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile smoke --stage grid --only epsilon_x_half_life
```

大范围单因素搜索（`search` 和 `broad` 都是 1,000 笔/arm）：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile search --stage oat
```

二维交互搜索：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile broad --stage grid
```

核心策略全组合（规模很大）：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile search --stage cartesian --only ewma_policy
```

用已有目录断点续跑：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --resume experiments/exp010_tdr_parameter_search/out/时间戳
```

端口被占用时可直接传递公共覆盖：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --set chain.base_port=9800
```

分批运行时，首次使用 `--max-trials 10` 仍会把完整计划写入 manifest；以后对同一
目录重复 `--resume ... --max-trials 10`，驱动器会自动选择下一批尚未完成的 trial：

```powershell
python experiments/exp010_tdr_parameter_search/run.py --profile broad --stage all --max-trials 10
python experiments/exp010_tdr_parameter_search/run.py --resume experiments/exp010_tdr_parameter_search/out/时间戳 --max-trials 10
```

只重新绘图：

```powershell
python experiments/exp010_tdr_parameter_search/plot_results.py --run experiments/exp010_tdr_parameter_search/out/时间戳
```

## 输出

每次搜索生成独立的 `out/<timestamp>/`：

- `manifest.json`：完整搜索计划和参数快照；
- `trials/<id>/trial.json`：每个唯一配置及五个 arm；
- `trials/<id>/replicate_k/.../summary.json`：原始实验结果；
- `raw_results.csv`：逐 trial、逐重复、逐方案数据；
- `aggregate.csv`：中位数、最小值、最大值；
- `comparisons.csv`：EWMA 相对 plain/valve 的直接比较；
- `five_way_comparisons.csv`：每个 trial 的五方案 Relay、TDR events、
  TDR transfers、Relay+events、Relay+transfers 及并列最优方案；
- `parameter_value_impacts.csv`：每个 OAT 参数值下五种方案的 Relay率、
  TDR 事件数、再平衡转账数、Relay+再平衡转账数、搬运量、吞吐和延迟；
- `parameter_effects.csv`：各参数的影响幅度、影响等级、排名和观测最佳值；
- `recommended_policy_values.csv`：只列策略参数的候选最佳值；场景参数不会被
  错当成算法最优值；
- `analysis_summary.json`：影响判定阈值和最佳值选择规则的机器可读说明；
- `leaderboard.csv`：优先按压力是否充分、EWMA 是否独占 Relay 最优、
  是否击败 Valve，再按 Relay 与 Relay+transfers 排序；
- `pareto_front.csv`：按 EWMA Relay、搬运次数、搬运量计算的非支配候选；
- `figs/oat/*.png`：每个参数的 Relay、再平衡、搬运量和吞吐曲线；
- `figs/grids/*.png`：二维参数热力图；
- `figs/parameter_values/*.png`：每个参数不同取值对五种方案的影响曲线；
- `figs/parameter_importance.png`：参数影响强弱排序；
- `figs/pareto_relay_vs_transfers.png`：Relay–再平衡 Pareto 散点图；
- `figs/five_way_baseline.png`：固定压力基准下五方案的 Relay、TDR events、
  TDR transfers 与 Relay+transfers 四项柱状图；
- `figs/leaderboard_ewma.png`：严格击败 valve 的 EWMA 候选排行。

## 如何判断参数影响和候选值

参数影响不使用隐藏权重。`parameter_effects.csv` 同时报告：

1. 不同取值造成的 Relay 率跨度；
2. 每笔 CTX 对应的再平衡次数跨度；
3. 搬运 ETH、完成吞吐和 EWMA 相对 Valve 优势的跨度。

总体影响取前两项的较大值。跨度达到 10 个百分点为 `large`，3 个百分点为
`medium`，1 个百分点为 `small`，更低为 `negligible`。`events` 是 TDR
触发次数；`transfers` 是实际完成的再平衡转账次数，一个事件可包含多笔转账。
两种综合口径分别是 `relay_plus_events` 和 `relay_plus_transfers`，优先用后者
衡量执行开销。候选最佳值要求同场 Plain 的 Relay 率至少 2%、数量至少 20，目标策略
确实触发 TDR，且门禁通过；然后依次最小化 Relay 率、Relay+transfers、
搬运 ETH，最后最大化完成吞吐。不满足压力条件时推荐文件留空，不再把
“关闭策略”误报为最佳参数。余额和金额上下限仍属于场景敏感性，不能直接
宣称为算法最优参数。

`parameter_effects.csv` 还会输出 `minimum_replicates`、`evidence_quality`、单个参数值
内部的 Relay 波动和 effect-to-noise ratio。单次运行标记为 `exploratory`；至少三个
route seed 才标记为 `replicated`。因此 1,000 笔单次搜索用于筛选，最终候选仍应以
`--replicates 3` 或更多重复复验。

## 结果使用限制

Broad 档只有一个路由种子，只能筛选，不能作为最终论文结论。推荐从 Pareto
前沿选择 5～10 个候选，再使用 40,000 笔、至少 5 个 route seed 复跑。不要在
同一数据窗口无限调参后直接报告最优点；至少保留另一段 trace 作为验证集。
