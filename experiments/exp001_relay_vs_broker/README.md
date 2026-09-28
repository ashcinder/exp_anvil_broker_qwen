# exp001 · relay vs broker 机制对比

**测什么**：同一批真实 ETH 主网跨片交易（每行走两条路径各一次、逐对交替消序），
用链上实测数据检验三条假设：

- **H1 延迟相等**：broker 与 relay 都是 Θ1→Θ2 两跳串行 → e2e 时间/入块跳数应≈；
- **H2 relay 跨片通信显著更高**：relay 的 mint 段依赖"Θ1 已入块"的跨片证明
  （账单 = Θ1 RLP 字节 + 回执证明代理，全实测）；broker 路径跨片只有一条
  小额控制消息（唯一模型常数 `exp.match_ctrl_bytes`，默认 100 B）；
- **H3 链上足迹相等**：两路各 2 条段真实入块 → relay 的额外开销在**跨片消息**
  而不在链上（论文对比口径）。

**路由**：broker 方案经共享匹配策略 static_broker（`brokerlab/matching.py`）：
eligible=目的余额≥v，种子 RNG 均匀选一个；`scale.num_brokers>1` 时真实分流，
=1 时退化为恒选 0 号。CSV 带 broker_id 列。

**运行**：`python run.py`（同目录 `config.yaml` 定参数与数据源；
临时覆盖 `--set exp.pairs=20` 等）。前提：`trace/ETH_cleaned.csv` 存在
（先跑 `python -m brokerlab doctor --config experiments/exp001_relay_vs_broker/config.yaml` 体检）。

**产物**（`out/<时间戳>/`）：`ctx_rows.csv`（逐笔全度量源数据）、
`summary.json`（聚合 + 假设判定）、`figs/exp001_overview.png`（延迟 ECDF /
跨片账单 / 链上足迹三面板）。退出码 0 ⇔ 三假设全部 VERIFIED 且守恒无违约。

**最近结果**：`out/20260904_024203/`（2026-09-04，匹配器接入后正式跑），50 对 × 2 = 100 笔
全成功、全局净和 0 wei：H1 相对差 2.5%（两次前跑分别为 7.4%/0.4%，秒级延迟均值属抖动量级）；
H2 2005 B vs 100 B = 20.0×；H3 各 100 段完全对称。三跑对照结论：字节/段数类指标逐位稳定，
延迟均值随机器负载浮动——论文引用取前者，延迟报均值±重复次数。
