# 实验 22：双分片单向转账、余额耗尽与 broker 收益

同一份确定性的合成 A→B 用户跨片转账流，在两条独立 Anvil 双分片链上分别运行 Plain（无 TDR）与 Proportional TDR。每条链只有一个 broker；两个臂的手续费和初始余额相同。流不宣称是主网真实方向分布。

默认 15,000 笔，每笔 0.02 ETH、30 笔/秒、A/B 各 180 ETH 初始 broker 余额。总本金需求 300 ETH，因此无 TDR 时 B 侧约在 9,000 笔 broker 服务后无法继续付款，之后转 Relay。启用 TDR 时 A 侧积累资金，可经真实 burn/mint 搬到 B。实际首次 Relay 时间和收益以链上实验为准，不能预设原论文图中的数字。

两臂均开启 B2E：名义费用 F = 21,000 × 100 gwei = 0.0021 ETH，每笔 broker 服务留存 40%，即 0.00084 ETH；Relay 不给 broker 收益。Anvil gasPrice 为零且 `charges_rebalancing=false`，本实验纵轴为**broker 留存手续费累计额**，不是扣除真实 gas 后的经济净利润。费用和本金都真实进入 A 侧 broker 账户，所以 A+B 链上余额随留存费用增加。

## 运行

在项目根目录：

```powershell
python experiments/exp022_directional_profit/run.py --dry-run
python experiments/exp022_directional_profit/run.py --smoke
python experiments/exp022_directional_profit/run.py
```

如果 TDR 臂因运行时限等环境原因失败，而 Plain 已通过校验，可只重跑 TDR：

```powershell
python experiments/exp022_directional_profit/run.py --reuse-plain experiments/exp022_directional_profit/out/<上次时间戳>
```

恢复模式核对除 `exp.drain_tail_s` 外的全部配置与工作负载 SHA256，然后复核旧 Plain 原始 CSV；新输出的 `summary.json` 会指向旧 Plain 文件目录。默认收尾时限为 3600 秒，以容纳本机较慢的链上派发。

`--smoke` 运行配对小样本并产出相同格式的图和 CSV，用于管线检查；正式结果只使用无此参数的运行。可用 `--set exp.count=...` 等覆盖参数；输入摘要和配置写入 `summary.json`。输出在 `experiments/exp022_directional_profit/out/<时间戳>/`：

- `workload.csv`：两个臂共用的有序合成转账流；
- `plain/`、`tdr/`：逐笔 CTX、真实 TDR 搬运及链日志；
- `balance_by_block.csv`：按真实确认块重建的两个子账户余额；
- `profit_by_tx.csv`：按累计已确认用户 CTX 数计算的 broker 留存费用；
- `figs/balances.png/.pdf`：无 TDR 的 A/B 余额随块高变化；
- `figs/profit.png/.pdf`：TDR on/off 的累计收益对照；
- `summary.json`：两臂完整性、链上余额对账和指标。

余额重建按 Θ1 确认块给 A 加本金与 broker 留存费用，按 Θ2 确认块从 B 扣本金，并按 TDR burn/mint 的各自确认块搬运。失败、漏单、账实不符、TDR 丢失、TDR 事件缺少块高或两臂工作负载不一致均令实验失败。TDR 转移不是用户 CTX，不计入收益图横轴。
