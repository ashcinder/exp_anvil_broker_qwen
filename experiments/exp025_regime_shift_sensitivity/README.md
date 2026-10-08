# 实验25：突变负载下的参数敏感性

沿用 `submit_regime_shift` 的选流和人工目的分片轮换，测试已有 Top-up + EWMA。
从 `trace/ETH_cleaned.csv` 选出 40000 笔用户 CTX，每个参数点运行 10 轮。
轮次在外层交错；同一轮的参数点使用相同 route seed，十轮依次为 7、17、…、97。
每次运行独立 Anvil 链；不把 broker 或单笔 CTX 当作重复样本。

| 扫描项 | 取值 | 固定值 |
|---|---|---|
| q_min | 0.1～0.9，步长0.1 | hl=10，epsilon=0.95 |
| 半衰期 | 1,2,4,6,8,10,15,20,25,30,35,40,45,50,100,150,200,250,300,400,500 | q_min=0.3，epsilon=0.95 |
| epsilon | 0.05～1.00，步长0.05 | q_min=0.3，hl=20 |

50 个点 × 10 轮 = 500 次运行，共 20000000 笔用户 CTX。
epsilon=1是边界对照：单侧Topup的缺货下界为0，非负可用余额不会触发补充，EWMA观测和目标计算仍保留。
已修复TDR转出回执查询被快循环不断推迟的问题；复测请开新运行，不复用修复前完成的任务。
范围写在 `search_space.yaml`，运行参数写在 `config.yaml`。
使用无限记忆 EWMA（有限窗口=0）、至少5块观测预热、事件间隔10块、单侧 Top-up。
半衰期不是硬窗口长度；这套结果也不是实验24的双侧触发结果。

在仓库根目录运行：

运行环境沿用原项目：安装 `requirements.txt` 中的依赖；`anvil.exe` 在PATH里，
或在YAML的 `chain.anvil_bin` 填写其路径；保留 `trace/ETH_cleaned.csv`。
仓库根目录是 `D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen`。
参数点的有效hl、epsilon、q_min由扫描表生成的方案覆盖基础配置，完整有效参数保存在每次摘要中。

```powershell
python experiments/exp025_regime_shift_sensitivity/run.py --dry-run
python experiments/exp025_regime_shift_sensitivity/run.py
```

只跑某一维，或续跑：

```powershell
python experiments/exp025_regime_shift_sensitivity/run.py --only q_min
python experiments/exp025_regime_shift_sensitivity/run.py --resume experiments/exp025_regime_shift_sensitivity/out/<运行目录>
```

续跑必须保留第一次运行的 `--only`、`--set` 和 `--smoke` 选项。
配置、扫描范围、源代码和选出的负载变更会拒绝续跑；已完成轮次会重新核对原始数据。
失败轮次重新执行，失败数据不进入最终曲线。

输出包括 `manifest.json`、`params.json`、`workload.csv`、`raw_results.csv`、`report.json`，
以及每次运行的 `ctx_rows.csv`、`tdr_moves.csv`、`balance_snapshots.csv`、`block_stats.csv`、
`balance_by_block.csv`、摘要和链日志。
仅全部计划轮次通过完整性和守恒校验后自动绘制三张双指标箱线图（搬运次数、Relay数）。

复用你已修改的 `D:\实验\exp8_latest_plotting\plot_exp8_latest.py` 的 `dual_boxplot`：

```powershell
python "D:\实验\exp8_latest_plotting\run.py" --experiment-source "<完整运行目录>"
```

绘图输出 `sensitivity_q_min_exp025`、`sensitivity_half_life_exp025`、
`sensitivity_epsilon_exp025` 的 PNG/PDF，并保存来源哈希。
可用 `--no-plot` 只运行实验，之后再用上述命令绘图。

`--smoke` 为80笔、1个broker、1轮的小规模管线检查，明确标记 smoke，不能作为论文正式数据。
基础交易记录来自真实数据，但热点变化、选流顺序和释放顺序属于构造负载，不是主网时间演化。
新增 `shift_avoid_same_shard: true`：热点目的分片等于源分片时，改用轮换表中下一个不同分片，
保留40000笔跨分片交易，不删除交易。此处修正了旧蓝本改写后可能产生同分片交易的问题。
当前协调器采用依赖引擎的余额增量消息和FIFO收口屏障，修复旧蓝本的接口不兼容；
不再用链上余额定时覆盖路由器的在途预留。与历史归档的数值不保证相同。
