# 实验8：Valve阈值与EWMA开销候选扫描

独立配置：`config.valve_sweep.yaml`。原`config.yaml`不变，正在运行的实验不受本次配置新增影响。
本次只创建配置，不自动运行，不修改历史结果。恢复原实验只需不指定新配置。

## 运行范围

每轮15组，2轮，路由种子7/17。每组40,000笔CTX，累计1,200,000笔，不含TDR搬运。
注入120 CTX/s，50 brokers、16分片，每broker每分片1.5 ETH；金额筛选0.25～3 ETH。
其他公共参数沿用原实验8。按照上一轮6组38.5分钟推算，约3～3.5小时，机器负载可能改变耗时。

|分组|参数|组数|
|---|---|---:|
|Plain|无再平衡|1|
|Valve|1.05、1.1、1.2、1.3、1.5、1.7、2、2.5、3|9|
|Proportional|ε=0.1，硬窗口6块|1|
|Topup硬窗口|ε=0.65，窗口6块，q_min=0.3，χ=2|1|
|Topup EWMA|ε=0.5、0.85、0.95；均hl=12、q_min=0.6、χ=2|3|

Valve=1.3保留为原主对照，新增所有阈值必须一并报告。EWMA=0.5为当前主候选；0.85/0.95为减少搬运的待验证候选。
在相同目标下，三组EWMA缺货触发线分别是目标余额50%、15%、5%；高ε可能补货过迟，不能保证Relay更低。
实验10的ε=0.95结果来自1000笔、余额1 ETH、hl=4、q_min=0.2；不能当作本配置新组合已验证的证据。

硬窗口token显式填写hl=0，防止旧的压缩标签将q_min误读为半衰期。日志中的EWMA=0表示关闭EWMA、采用硬窗口。
report.json的V2/V3仍只自动检查配置的主候选ε=0.5相对Plain，其通过不代表胜过所有Valve或其他方案。
其他候选须从每个session的summary.json或report.arms逐项比较，重点检查Relay、事件、搬运、R+T和守恒。
禁止将事件数等同于链上搬运次数；禁止只保留对EWMA有利的Valve结果。

## 运行

先等正在运行的实验8结束，避免同端口冲突。同机其他重负载实验也建议结束后再跑，以减少运行噪声。

项目根目录PowerShell：

```powershell
python experiments/exp008_tdr_schedule_final/run.py --config experiments/exp008_tdr_schedule_final/config.valve_sweep.yaml
```

已在实验8目录：

```powershell
python run.py --config config.valve_sweep.yaml
```

所有结果保存在新的`out/<时间戳>/`，不会覆盖旧结果。只用原配置运行时仍执行`python run.py`。

若只先验证一轮，可在上述命令后追加`--set exp.exp008_sessions=1`，此时约1.5～1.75小时；这不提供两轮证据。

## 解释限制

这是探索性比较，不是严格等参数的EWMA消融或已证明的最优调参。保持数据负载固定后比较各方法的最佳已测表现。
新候选在同一trace上胜出之后，仍需独立时间窗口和更多种子验证，不保证存在同时最少Relay及最少R+T的参数。
本次没有修改共享算法代码或修复实验10的小数余额CLI解析问题。
