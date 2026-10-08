# 实验 21：TDR 单因素消融（实验 8 的 8 万笔 clean 负载）

实验 21 复用实验 8 的链、账户、资金、clean trace、交易金额范围、路由种子、注入速率、资源上限和完整性校验。与实验 8 配置相比，除实验名称/报告字段外，只修改五个对照臂与全局默认 ε：所有启用 TDR 的对照臂均为 **ε=0.95**。每臂 80,000 CTX、50 个 broker、16 个分片、初始余额为每 broker 每分片 150 ETH，只运行 **1 场**。五个臂合计执行 400,000 次 CTX；不是总共 80,000 次。

| 方案 | 余额调度策略 | 需求估计 | ε | 作用 |
|---|---|---|---:|---|
| Plain | 不启用 TDR | 不适用 | 不适用 | 整体 TDR-off 参照 |
| Proportional + hard | 比例重排 | 20 块硬窗口 | 0.95 | 同时关闭 Top-up 与 EWMA |
| Proportional + EWMA | 比例重排 | EWMA 半衰期 20 块 | 0.95 | 只关闭 Top-up |
| Top-up + hard | 只补缺口 | 20 块硬窗口 | 0.95 | 只关闭 EWMA |
| Top-up + EWMA | 只补缺口 | EWMA 半衰期 20 块 | 0.95 | 完整方案 |

四个 TDR 臂形成 2×2 因子设计；与完整方案相邻的两组比较每次只改变一个因素。Plain 不使用 ε，因此“统一 ε”只适用于启用 TDR 的四个臂。所有臂在同一场内使用相同的有序交易流与 route seed，但各臂启动独立链。

## 主指标及解释

主指标为 `Relay-routed customer CTXs + completed TDR transfer requests`，对应输出中的 `relay_plus_tdr_transfers`，越低表示在这个复合计数口径下开销越小。这里一笔成功 TDR 转移按 **1** 次请求计，虽然它通常产生 burn/mint 两条链上腿；TDR 转移也**不是一笔用户 CTX**。因此这个和数不能命名为“总交易数”或“总链上交易数”。报告另列 Relay、TDR 成功转移数、事件数、搬运金额和吞吐，避免复合指标掩盖权衡。

`comparisons` 给出关闭组件后的主指标减去完整方案的值；正数表示完整方案在该指标上更低，负数则相反。只运行一场，属于配对负载下的描述性消融，不计算显著性或置信区间。

## 运行

在已安装项目依赖、能够运行 Anvil 的 `brokerlab` 环境中，进入本目录：

```powershell
cd D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp021_tdr_ablation
python .\run.py --dry-run
python .\run.py
```

先用 `--dry-run` 确认 5 个臂、80,000 CTX/臂和 clean trace 路径；它不启动链，也不写输出。正式运行可能需要较长时间，请勿在已有实验占用 8600～8615 端口时同时启动。

结果写入本目录 `out/<时间戳>/`：

- `report.json`：配置快照、完整性状态、五臂指标及相对完整方案的差值；
- `ablation_metrics.csv`：可直接用于论文绘图的逐臂数据；
- `session_1/<时间戳>/`：exp003 管线的原始逐笔 CSV、搬运 CSV、summary 和链日志。

若任何臂未完成、校验失败或 CSV/summary 数量不一致，实验 21 返回非零退出码，`report.json` 标记 `failed`，不把不完整结果当作有效消融数据。
