# 实验27：两个分片、同一broker的单向转账余额失衡

默认只启动两个Anvil分片、一个broker。Plain与Top-up+EWMA各跑1000笔、1轮，
每个子账户初始45 ETH，120笔/秒。两臂共用同一负载、路由种子7，分别启动独立链。
这是小规模机制演示：重点是TDR关闭时源侧增加、目的侧减少，以及开启TDR后的搬运。
EWMA半衰期20块、epsilon=0.95、q_min=0.3，最小事件间隔10块。

从`trace/ETH_cleaned.csv`按两分片地址映射，筛选真实0→1转账；
保留原交易顺序、唯一ID及原金额，仅筛选0.01～0.2 ETH的小额转账，不改写交易目的分片或缩放金额。
此前0.01～10 ETH负载中的多笔5～10 ETH交易在最初几块就消耗了45 ETH目的侧余额，
后续Relay不改变broker余额，因而大部分曲线呈水平。小额筛选用于观察逐步分化。
金额上限从0.1提高至0.2 ETH，目的是让目的侧流动性更早不足；实际出现的区块位置由选流和执行结果决定。
不要求余额精确归零；若1000笔总需求不足45 ETH，仍可展示一增一减，且不强制产生TDR搬运。
与实验26统一开启B2E：费基数21000×1000 wei，broker留存10%，TDR不收B2E费用。
因此源侧余额还包含真实留存手续费。修改方向可在YAML中交换source_shard和destination_shard。
与实验26保持相同初始余额、EWMA参数、费用设置和单轮配对设置；
区别是2分片、1broker、1000笔、0.01～0.2 ETH小额筛选和固定0→1单向负载，不采用人工热点轮换。
120笔/秒是负载释放速率，单broker的实际处理速度由确认及执行过程决定。
`drain_tail_s=1200`允许单broker排空积压；每臂总时限约为1000/120+1200秒。
若仍超时，失败原因写入报告及`execution_summary.json`，不将部分数据作为完整实验绘图。

Plain的broker服务在源侧收款，在目的侧付款，因此两个子账户逐渐分化。
目的侧不足时转为Relay，Relay不再消耗broker目的侧余额；这不是所有子账户同时耗尽。
Top-up+EWMA使用项目现有TDR算法，把源侧累积余额搬往目的侧；保留实际搬运记录。

由于只有一个broker，`balance_by_block.csv`即该broker两个子账户的账本余额，
不存在多个broker求和的混淆。余额按实际Theta1/Theta2和TDR burn/mint确认块重建；
各分片区块高度独立，不声称是严格同步的墙钟观测。
原始逐笔记录、TDR搬运账本、EWMA余额快照、守恒结果全部保留。

在实验27目录运行（本次仅完成代码，未运行实验）：

```powershell
python .\run.py
```

也可在仓库根目录运行：

```powershell
python experiments/exp027_two_shard_directional_balance/run.py
```

参数全部写在`config.yaml`。需要原项目依赖、Anvil和真实CSV。
每次运行创建新的`out/<时间戳>/`；不支持续跑，失败后新开一次。
完成两臂并通过数据校验后自动绘制`figs/broker0_two_shard_balances.png/.pdf`。
也可`--no-plot`只导出数据，随后手动绘图：

```powershell
python .\plot.py --source "<完整运行目录>"
```

论文Figure 1右图（仅Plain，同一broker的两个子账户）：

```powershell
python .\plot.py --source "<完整运行目录>" --paper-right --output "<绘图输出目录>"
```

输出`fig1b_broker0_two_shard_balance.png/.pdf`及来源哈希。
论文右图默认横轴现在为交易数（`--x-axis transactions`），从0到本轮总交易数。
按`arrival_pos`输入顺序累计成功交易的结算金额：broker交易同时计入两侧已结算收付和留存费用，
Relay交易也计入横轴，但不改变broker余额。不是把区块高度按比例换算成交易数，
也不是某个墙钟时刻的链上快照；并行交易两阶段的在途差异不在此坐标中展示。
导出`fig1b_balance_by_transaction.csv`，终值须与原区块余额账本精确一致。
红框在这个模式中标记目的侧余额低于最小转账金额的交易序号区段。
需要原来的区块横轴时，添加`--x-axis blocks`。
双面板Plain/Topup对照图仍使用区块横轴，保留TDR实际burn/mint位置。

对已经完成的最新运行，只重新绘图（不执行实验）：

```powershell
python .\plot.py --source ".\out\20261004_211649_080507" --paper-right --output ".\out\20261004_211649_080507\figs_transactions"
```

红框标记目的侧余额低于本实验最小转账金额0.01 ETH的区段，不声称实际余额恰为零。

数量可通过YAML的`ctx_per_broker`调整，`max_backlog`应不小于所需的最大积压。
该单轮配对实验用于展示机制，不作为重复参数实验或统计置信区间。
