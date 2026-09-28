# 实验11：Clean 数据上的 EWMA + TDR 复验

## 一条命令运行

在已经激活 brokerlab 环境的 PowerShell 中，从任意目录执行：

```powershell
python "D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp011_clean_ewma\run.py"
```

所有参数已固化到本目录 config.yaml，不需要额外 --set。
不要与使用相同 8600～8615 端口的实验同时启动。

仅检查安装、CSV 路径、配置和运行计划（不启动链，不读取 CSV 内容）：

```powershell
python "D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp011_clean_ewma\run.py" --check
```

## 固定参数

| 参数 | 设置 |
|---|---|
| 数据集 | trace/ETH_cleaned.csv，排除合约端点 |
| 金额过滤 | 0.01～10 ETH，不裁剪金额 |
| 分片 / Broker | 16 / 50 |
| 余额 | 每 Broker 每分片150 ETH |
| 交易数 | 每方案40,000笔，800×50 |
| 轮数 | 2轮，五方案，总计10次方案运行 |
| 目标速率 | 120 CTX/s（块时间1秒），不保证实际达到 |
| 硬窗口 | 20块 |
| EWMA | epsilon=.95，半衰期20块，q_min=.10 |
| Valve | 容量阈值1.3 |
| Proportional / 硬窗口 Topup | epsilon=.10 |
| 事件最小间隔 / 超时 | 10 / 20块 |
| 启动错峰 | 3 + broker编号 % 5块 |
| sender_window / max_inflight | 2 / 24 |
| trace种子 / 两轮路由种子 | 42 / 7、17 |
| B2E | 关闭 |

配置基于老师 exp010_param_grid 的 F1 正式档，不使用当前 exp008 的弱热点低余额配置，
不拼接未经联合测试的 q_min=.15 与半衰期60。
当前代码必需的 coordinator_mint_budget_eth_per_shard=2000000 和
match_ctrl_bytes=100 已补齐。前者是技术签发账户预算，不是 Broker 流动性。
未设置固定供钱侧 epsilon，各臂分别使用自身 epsilon。

## 实现与输出

run.py 直接加载实验8入口，将其默认配置与输出根目录重定向到本目录，
保留实验8的两轮编排、7/17路由种子、配对判据及实验3真实链上执行流程。
未修改实验8、老师实验10和 brokerlab 核心代码。控制台内部仍会出现 exp008/exp003 字样。

产物位于本目录 out/<时间戳>/：report.json 及 session_1、session_2 下的
summary.json、逐笔记录与管线自带图。源代码被复用，因此未来修改共享代码也会影响实验11。

完整性/守恒校验必须通过；分析时同时报告 Relay、TDR事件、搬运次数、
逐轮 Relay+搬运、吞吐及延迟。事件不等于搬运次数，Relay+搬运不等于全部链上交易数。
通过 V2/V3 只表明相对 Plain 达到门槛，不代表对所有方法全面胜出。

这不是历史结果的逐数值复现：当前代码与种子安排不同，EWMA获胜并非保证。
硬窗口 epsilon=.1 与 EWMA epsilon=.95 的主对比不能单独归因于估计器。
纯估计器消融应另加同 epsilon=.95 的硬窗口组；本次按约定只运行五方案。

## 可恢复性

本次仅新增 experiments/exp011_clean_ewma，不覆盖任何旧实验、数据或结果。
停用时保留或移走本目录即可，不需要恢复原实验代码。已运行后请先保留 out 中的结果。
