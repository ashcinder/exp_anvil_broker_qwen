# 实验22：Clean 数据集上的 EWMA 参数搜索（10,000 CTX × 5 轮）

本实验沿用实验19/20的 Clean 数据、有限滑窗 EWMA 和单因素搜索执行器，但搜索
epsilon、半衰期、`q_min` 和触发间隔 X；滑动窗口 W 固定为20块，不参与搜索。
数据为 `trace/ETH_cleaned.csv`，排除合约端点，金额过滤范围0.01～10 ETH；固定
50个 brokers、16个分片、150 ETH初始余额、120 CTX/逻辑块。每个候选配置运行5轮，
路由种子为7、17、27、37、47；每轮每个方案10,000笔（50 brokers × 200 CTX）。

各参数候选值：

- deficit epsilon：0.05、0.10、…、0.95，步长0.05，共19个值；
- EWMA 半衰期：1、2、3、4、5、6、8、10、12、15、18、20、25、30、35、40、45、50块，共18个值；
- `q_min`：0.1、0.2、…、0.9，步长0.1，共9个值；
- TDR 最小触发间隔 X（`chi_blocks`）：1、2、3、4、5、6、8、10、12、15、18、20块，共12个值。

四组各自基准 epsilon=0.95、半衰期=20、`q_min`=0.1、X=10 会合并为一个共享基准，
去重后共 **55个参数候选**。目标 EWMA 工作量为55 × 5 × 10,000 =
**2,750,000 CTX**。执行器跨候选缓存相同路由种子下的对照臂，额外约运行
20 × 10,000 CTX；计入对照臂后约为 **2,950,000 CTX**。EWMA 在固定20块窗口内按
`2^(-块龄/半衰期)` 加权，窗口外贡献为0，预热块数固定为5。

## 运行

在仓库根目录 PowerShell 先查看计划（不启动链）：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\run.py --dry-run
```

计划应显示55个候选、5轮、每 arm 10,000 CTX。启动正式实验：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\run.py
```

输出位于 `experiments/exp022_clean_ewma_parameter_search_10k/out/<时间戳>/`。启动器
会自动寻找连续可用的16个 localhost 端口并写入清单，不会连接或结束占用端口的进程。
正式搜索全部成功完成后，会自动生成四个参数的论文风格箱线图和总览图。

中断后用实际输出目录恢复：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\run.py --resume .\experiments\exp022_clean_ewma_parameter_search_10k\out\<时间戳>
```

如需手工重绘四个参数的敏感性图：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\plot_sensitivity.py --run .\experiments\exp022_clean_ewma_parameter_search_10k\out\<时间戳>
```

如果要把四张箱线图的右轴改为 TDR 搬运金额（ETH），可加
`--boxplot-right moved_eth`；此选项只影响指定运行目录下的绘图，默认及正式
10,000-CTX 实验自动生成的图仍以 Relay CTX 为右轴：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\plot_sensitivity.py --run .\experiments\exp022_clean_ewma_parameter_search_10k\out\<时间戳> --boxplot-right moved_eth
```

旧5,000-CTX运行的搬运金额箱线图还可加 `--no-boxplot-note` 去掉每张图底部的说明文字；
该选项不改变正式10,000-CTX运行的默认绘图：

```powershell
python .\experiments\exp022_clean_ewma_parameter_search_10k\plot_sensitivity.py --run .\experiments\exp022_clean_ewma_parameter_search_10k\out\20260929_200536 --boxplot-right moved_eth --no-boxplot-note
```

仓库当前已有 `exp022_directional_profit`（单向转账收益实验）；本参数搜索放在
独立目录中保留原实验，运行命令中的目录名已区分二者。

此前的5,000-CTX运行 `out/20260929_200536/` 已保留作历史结果，不可用作本轮
10,000-CTX实验的续跑目录；新实验请直接启动命令，生成新的输出目录。
