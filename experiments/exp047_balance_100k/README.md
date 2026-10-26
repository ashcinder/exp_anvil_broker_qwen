# 实验47：100k CTX 的初始余额—利润敏感性

当前论文的余额利润箱线图使用本实验。图号随论文排版可能是 Fig5b 或 Fig5c，请以横坐标 `Initial balance (ETH)` 区分。

- 每子账户初始余额：15、30、45、75、100、150、200 ETH。
- 方案：topup（Broker with TDR）、plain（无再平衡）、Autoloop、Spider。
- 16分片、50 broker、200用户；每次运行100,000笔全系统用户CTX。
- 默认10轮，每档每方案10个完整运行样本，共280项任务。
- 第 r 轮 route seed 为 `7 + (r - 1) * 10`；各方案在同轮同余额共享工作负载与种子。
- 统计 Broker 19 的16个子账户之和。收益为 `served × 0.0021 Gwei − confirmed_internal_transfers × 0.021 Gwei`；链 gas price 为0，扣费使用统一模型成本。
- TDR：epsilon=.95、q_min=.3、EWMA half-life=20块、min_blocks=5、chi=10、timeout=20。
- Autoloop：容量2B、30%/70%阈值、目标B、10块检查。Spider：成功服务流水达到 `0.25 × 16 × B ETH` 后均衡。各方案保持原生调度。

## 安装

在仓库根目录、已激活的 Python 3.11+ 环境中执行：

```powershell
python -m pip install -r requirements-experiments.txt
anvil --version
```

需要安装 Foundry 的 Anvil，并将它放进 PATH；也可以给运行命令添加 `--anvil-bin C:/path/to/anvil.exe`。

仓库已包含 `reference/workload.csv.gz` 和 `reference/config.json`。压缩包解压后的100k工作负载与既有正式实验逐字节一致；脚本按 `reference/manifest.json` 校验，后端按 `exp045_plain_sensitivity_supplement/runtime/c/provenance.json` 校验。无需访问原作者电脑的 C:/D: 目录，也无需重新抽样。

## 只运行最后两轮：第9、10轮

先查看任务计划；该命令不会启动交易或创建实验输出：

```powershell
python experiments/exp047_balance_100k/run.py --start-round 9 --rounds 2 --dry-run
```

应显示 `rounds: [9, 10]`、`tasks: 56`、`route_seeds: [87, 97]`。正式执行：

```powershell
python experiments/exp047_balance_100k/run.py --start-round 9 --rounds 2 --base-port 43600 --lock-port 45955 --output experiments/exp047_balance_100k/out/round09_10
```

这会创建独立目录，运行 `7档余额 × 4方案 × 2轮 = 56项`，共5,600,000笔用户CTX。`--base-port 43600` 和 `--lock-port 45955` 为该独立实例指定端口，可与使用默认端口的原正式实验共存。每个独立并行实例需要不同的运行锁端口和输出目录。启动脚本会搜索16个空闲RPC端口。

如果要的是“从头只跑两轮”，将 `--start-round 9` 改为 `--start-round 1`，并另取输出目录；对应种子7、17。默认不传轮次参数仍是第1至10轮。

## 进度与续跑

- `out/round09_10/progress.md`：每15秒刷新。
- `out/round09_10/t/任务编号/a001/stdout.log`：单项详细输出。
- 输出目录已存在时用 `--resume`，不再传轮次参数：

```powershell
python experiments/exp047_balance_100k/out/round09_10/code/run.py --resume experiments/exp047_balance_100k/out/round09_10
```

续跑校验冻结代码、工作负载和已完成结果，跳过成功任务，为中断任务创建新的attempt。不要向同一输出目录同时启动第二个实例。

## 绘图

全部选定任务完成后自动生成：

- `out/round09_10/figures/balance_100k.pdf`
- `out/round09_10/figures/balance_100k.png`
- `out/round09_10/figures/broker19_observations.csv`
- `out/round09_10/figures/caption.txt`

手动绘制已完成的配对轮次：

```powershell
python experiments/exp047_balance_100k/plot.py experiments/exp047_balance_100k/out/round09_10 --partial
```

箱体为Q1–Q3，中线为中位数，须线为样本最小值到最大值；不叠加散点，不绘制潜在收益上界。只跑两轮时每组只有两个样本，不能在图注中写十轮。纵轴最多使用一个断轴，位置根据实际无数据区间确定，保留所有观测值。
