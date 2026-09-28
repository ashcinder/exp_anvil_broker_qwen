# exp002 · broker 资金耗尽基线（无 TDR，真实数据）—— 对照手稿 fig1 / Experiment A

**测什么**：真实 ETH 主网 trace 的**多数方向**组成定向流（方向性=筛选所得，
符合手稿 "controlled stress regime" 定位），无 TDR，逐笔按 static_broker 规则
服务（目的子账户余额够 → broker 路径；不够 → relay/burn-and-mint 兜底），
全部真实上链。检验手稿四条结论：

- **C1** 空间失衡：broker 总额守恒（漂移应为 0），但目的分片余额趋零（fig1b）；
- **C2** relay 回退出现后占比递增（fig1a 柱状）；
- **C3** 跨片传输数据量随之上升（fig1a 红线；通信口径 = exp001 的实测标准）；
- **C4** 加大初始充值只推迟、不消除耗尽（两档余额对照，Introduction 论断）。

**路由**：每笔 CTX 走共享匹配策略 static_broker（`brokerlab/matching.py`）：
eligible=目的余额≥v 的 broker，种子 RNG 均匀选一个；无 eligible → relay 回退并计
`no_qualified`。`exp.balances` 语义 = 每 broker 每分片垫资，资金池 = N × 档位。
两条件共用同一 `exp.seed` ⇒ 选择序列跨条件可比。当前验证配置 num_brokers=1。

**运行**：`python run.py`（参数/数据源见 config.yaml；`--set exp.count=200` 等可覆盖）。

**产物**（`out/<时间戳>/`）：每条件子目录 `bal<档位>/drain_trace.csv`（逐笔源数据）
+ logs，顶层 `summary.json`（含 C1–C4 判定与 burn≡mint 对账）、
`figs/drain_vs_manuscript.png`（与手稿 fig1 同构双面板）。
退出码 0 ⇔ 四结论全部判"一致"且无失败 CTX。

**最近结果**：`out/20260904_024358/`（2026-09-04，匹配器接入后正式跑），150 笔（436 ETH 流）
×{100,300} 两档，300/300 成功 0 失败、burn≡relayed：C1 dst 终值 0.0013 ETH、总额漂移 0 wei；
C2 四分位 relay 份额 5→84→97→100%（首次回退 idx=34）；C3 每笔 202→2009 B（9.9×）；
C4 首次回退 idx **34 → 103**（×3.03 ≈ 余额比 ×3.0，近线性），仍耗尽至 0.0165 ETH。
**跨跑稳定性**：执行序指标（首次回退 idx=34、份额、终值 0.0013）与匹配器接入前的 09-03 跑
逐位一致——num_brokers=1 下匹配层退化为恒等选择，已实证；块号类指标（推迟 ×3.36→×4.31→×2.98）
有 ±40% 抖动，论文引用优先用执行序口径。
本实验是 M4（TDR 接入）的对照组：靶曲线即图 (b) 的橙色线。
