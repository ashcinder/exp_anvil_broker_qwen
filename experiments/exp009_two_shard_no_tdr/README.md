# exp009 · 双分片 TDR 开关与余额演化

这个实验只启动两个分片，使用真实 trace 中数量较多的单向跨片流。
`config.yaml` 中的 `tdr.enabled` 是真实执行开关：

- `false`：普通动态 broker，目的分片耗尽后回退 relay。
- `true`：使用 proportional TDR，执行真实 burn/mint 再平衡交易。

运行：

```powershell
python run.py
```

临时调整交易数或初始余额：

```powershell
python run.py --set exp.count=200 --set exp.balances_eth=200
```

产物位于 `out/<timestamp>/`：

- `tdr_off@<balance>/` 或 `tdr_on@<balance>/`：`ctx_rows.csv`、`tdr_moves.csv`、
  带 burn/mint 块高的 `tdr_move_events.csv`，以及 TDR 开启时的
  `balance_snapshots.csv`（单 broker 余额、τ、上界、确认事件）。
- `balance_trace.csv`：两个分片的单 broker 余额。
- TDR 关闭：`figs/fig_tdr_off.png/pdf`，画同一 broker 在两个分片的余额。
- TDR 开启：`figs/fig_tdr_on.png/pdf`，画目标分片当前余额、τ、
  `τ+ετ` 上界和真实确认的 TDR 事件红点。
- `summary.json`：broker/relay/TDR 数量、守恒校验和文件路径。

只用既有 CSV 重画：

```powershell
python run.py --plot-only out/<timestamp>/balance_trace.csv

# TDR-on 快照重画（例如目标分片 0）
python run.py --plot-only out/<timestamp>/tdr_on@150/balance_snapshots.csv --target-shard 0
```
