# 实验29：仅新增 epsilon / q_min 点，默认10轮

默认仅运行用户新增的11个点，不重跑原有参数点：

|扫描|新增值|固定值|重复|
|---|---|---|---|
|epsilon|0.96,0.97,0.98,0.99,1.0|q_min=0.3，半衰期=20块|10|
|q_min|0.01,0.02,0.04,0.05,0.07,0.08|epsilon=0.95，半衰期=10块|10|

默认共11个参数点×10轮=110次运行，共440万用户CTX。每轮使用相同route_seed，
十轮依次为7,17,…,97；轮内执行顺序确定性随机打乱，每次独立Anvil链，串行执行。
使用`--rounds 1`可改为单轮初步观察。

只运行epsilon=1的10轮：

```powershell
python .\run.py --epsilon-one-only --dry-run
python .\run.py --epsilon-one-only
```

该选项固定半衰期20块、q_min=0.3，共10次×40000=40万用户CTX；
不会运行其他epsilon或q_min点。与实验25对应轮次的route_seed一致。


16分片、50 broker、200 user；每broker每分片45 ETH，rate=120 CTX/s；
Top-up + 无限记忆EWMA，至少5个观测块预热，事件间隔10块，超时20块，B2E关闭。
前30%保留，后70%热点目的分片11/3每1600笔轮换，避免源目的同分片。
主指标Relay+TDR（每次完成搬运计一次），同时报告Relay；不修改负载来追求U型。

## epsilon=1 的精确边界

原brokerlab严格要求epsilon<1，新实验使用其本地隔离副本，允许**单侧Top-up**的epsilon=1。
此时缺货触发条件为available < (1-epsilon)*target=0，正常非负余额下永不触发TDR。
仍使用同一个Top-up引擎，保留需求观察、块轮询与目标计算；不切换plain、不替代成0.999…。
epsilon=1若实际搬运次数非零，会拒绝该次结果。epsilon>1或双侧Top-up的1仍拒绝。
epsilon<1的计算与原实现保持一致，并有对照测试及Windows spawn测试。

pipeline、tdr_hook与tdr_engine来自当前仓库的隔离副本，来源哈希在vendor_sources.json；
共享brokerlab与既有exp025/exp028源码不被修改。完整执行源码哈希写入每次manifest。

## 运行、续跑

在brokerlab环境：

```powershell
Set-Location 'D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp029_added_parameter_sensitivity'
python .\run.py --dry-run
python .\run.py
```

若已有实验进程仍在运行，使用其实际Python PID排队：

```powershell
python .\run.py --wait-for-pid <已有实验PID>
```

会先保存workload和manifest，然后等待该进程及其启动时的子进程结束。
等待中不启动新Anvil、不中断现有实验。等待完成后重新核对源码指纹，若源码被改则拒绝启动混版本实验。

恢复失败/中断运行：

```powershell
python .\run.py --resume '.\out\<原时间目录>'
```

保持原来的--only、--include-half-life、--epsilon-one-only、--rounds、--set等选项。未完成任务重新执行；
完成任务核对原始台账后跳过。配置、选流、代码更改会拒绝续跑。本次修改默认轮数及runner源码后，历史运行目录的源码指纹不匹配，应启动新的结果目录；不编辑旧manifest或覆盖旧结果。

后台启动信息保存在launch.json；日志在logs/，manifest的execution_status显示
preparing、waiting_for_existing_experiment、running、failed或complete。

## 分析

全部完成后自动输出analysis目录，包括CSV和Relay与Relay+TDR双纵轴趋势图。
默认每点10次；指定`--rounds 1`时不画箱体、不生成配对置信区间，均值与中位数就是该次观测。
有重复数据时会输出箱线图，至少3个共同完整轮次时才给出探索性配对区间。
运行中也可以分析已经完成的点：

```powershell
python .\analyze.py --source '.\out\<时间目录>'
python .\analyze.py --source '.\out\<时间目录>' --verify-raw
```

每次运行须通过CTX完整性、无重复无损失、链与账本对账、burn/mint守恒与TDR台账检查。
只统计通过校验且manifest标记complete的运行；不制造缺失样本。
探索性bootstrap区间未校正赢家选择/多重比较，不当作正式显著性结论。

本实验默认仅有新增区间；不能仅凭局部单调就断言整个参数范围没有U型。
epsilon=1的关闭边界也需与正常触发区域区分。不自动合并旧实验数据。

## 可选半衰期重复

若确实需要同时将exp028的11个半衰期点各运行10轮，使用：

```powershell
python .\run.py --include-half-life
```

这会新增110次，合计220次。默认不启用，以遵循“只跑新增参数”的范围。
只运行半衰期重复可用`--include-half-life --only half_life`。
指定`--rounds 1`时合计22次。不同配置使用独立manifest，不修改已经开始的任务网格。

离线检查：`python .\check_design.py`。`--smoke`是80CTX、1broker、1轮的管线检查，
包含epsilon=1边界，不作为正式数据；已有实验运行期间也不要并行执行smoke。
