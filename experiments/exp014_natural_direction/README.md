# 实验14：原始以太坊交易的 broker 子账户余额实验

当前配置只运行 EWMA Topup，epsilon=0.5、Q_MIN=0.3、TDR最小启动间隔5块，半衰期20块，一轮80000笔跨片交易。50个broker、16分片，每个子账户初始45 ETH；出块时间5秒。最终生效参数以config.yaml及运行输出resolved.yaml为准。

## 数据与映射

原始数据为项目根目录trace/ethereum_202509.csv，数据集需自行准备，不随Git仓库提交。允许合约端点，过滤错误及创建合约记录、模拟账户冲突和重复交易ID。金额范围10～100 ETH只用于筛选，不裁剪或放大原始金额。候选池100000笔，保持原顺序选择前80000笔跨片交易。

地址按int(address,16)%16固定映射，不使用热点地址重新映射。全部天然跨片方向保留；真实地址压缩为200个模拟用户账户。实验重放交易方向与ETH value，不重放完整原始合约状态。

## 运行

在已安装依赖及Anvil的项目根目录执行：

```powershell
python experiments/exp014_natural_direction/run.py --dry-run
python experiments/exp014_natural_direction/run.py
```

--dry-run仅打印计划。正式运行先校验不可变原始CSV副本、筛选并冻结workload，再启动独立链。每次输出到新out目录。也可使用--prepare-only仅准备数据，或--prepared指定已有准备目录；数据指纹不符时拒绝复用。

配置exp.ewma_epsilons控制epsilon列表，当前为[0.5]；设为null则使用ewma_epsilon。sessions=1表示每个策略一轮。多组策略共享同一冻结负载，但各自重新初始化链和余额。

## 绘制全部子账户

```powershell
python experiments/exp014_natural_direction/plot_balances.py --arm-dir "<实际策略输出目录>" --max-block 400
```

策略目录须含balance_snapshots.csv及tdr_move_events.csv。默认从策略目录名读取epsilon，也可显式指定--epsilon；--out-dir可指定图集输出位置。生成50个broker总览PNG/PDF及800个子账户PNG，并提供index.html筛选浏览。

蓝线为已确认余额，橙线为最近一次决策目标，灰色边界为(1±epsilon)*tau，红色倒三角标记已确认的TDR转出，绿色正三角标记已确认的TDR转入。执行期与冷却期目标仍可能保持旧值；它不是逐块重新计算的实时目标。不同分片有独立区块时钟，确认块高差不能直接等同于精确耗时。

## 实现与结果口径

EWMA Topup默认使用缺货下界和盈余来源上界配对，不是单独超过上界就必然触发。TDR正在执行时，同一broker不启动下一轮；chi从上次启动计时。已修复回执查询被快速循环不断推迟的问题，只有真正查询后才设置下次查询时间。

metrics.csv、summary.json及原始台账用于校验成功率、资金守恒、Relay与TDR开销。TDR事件可能包含多笔资金转移，每笔正常包含burn/mint两段，不能混淆事件数、转移数及链上段数。仅单方法实验不能识别相对其他方法的性能优势。

运行数据集、结果、生成图集、审计统计和本地备份均不上传GitHub。

## 时间预算与异常诊断

rate 的单位是 CTX/区块，而不是 CTX/秒。运行预算同时考虑区块释放时间、
同一 (sender_idx, src_shard) 的 sender_window 限制，以及两段转账的排队时间，
再加 drain_tail_s。预算是根据冻结 workload 估算的运行上限，不是已测量的耗时；
正常完成会提前结束，RPC、TDR 竞争和慢出块仍会消耗额外余量。

当前 80000 笔、120 CTX/块、5 秒/块、sender_window=2 的自然负载中，
最繁忙的发送者组有 1411 笔，按两段确认估算完成需要 1413 块。
因此时间预算为 1413×5+1800=8865 秒，而非仅按释放计算的 5135 秒。
这是该 workload 的估算；其他负载会自动重新计算。

超时后停止新派发，但继续处理在途 burn/mint 请求及真实回执；
异常收口宽限根据引擎、relay、TDR 超时参数计算，当前为 170 秒。
超时运行始终标记为不完整，不会因为收到部分结果而通过验收。

策略目录中的 execution_budget.json 记录预算；coordinator_progress.json
每约 30 秒记录派发、落定、等待队列、发送者组与在途 broker/CTX 编号。
已收到的 broker 信封保存在 engine_results/broker_XX.json；
failure.json 记录异常和缺失 broker。这些部分文件不能当作成功实验的全量余额图。
