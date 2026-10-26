# exp043：单图专用、统一条件的 SOTA 与组件对照实验

版本：exp043-unified-tradeoff-v1。所有点重新运行，不合并exp021/exp033的旧结果；只有一个Plain、一个完整TDR。数据由原执行管线产生，新增包只注册三个清晰别名并固定比较协议，不修改brokerlab或旧实验文件。没有承诺任何方法一定赢或一定盈利。

## 直接运行（本机D盘）

```powershell
Set-Location 'D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen'
python experiments/exp043_unified_tradeoff/run.py --dry-run
python experiments/exp043_unified_tradeoff/preflight.py
python experiments/exp043_unified_tradeoff/run.py
```

默认14方法×3轮×40,000用户CTX，共42任务、1,680,000个评估用户CTX。自动串行，每任务独立链。当前已验证计划、真实数据提取和PnP资金可行性；12个协议测试通过。未替你启动完整链上运行。

小型链路检查（可选，不得用于论文正式图）：
```powershell
python experiments/exp043_unified_tradeoff/run.py --smoke
```
一场完整负载试跑（可选，属于分析档）：
```powershell
python experiments/exp043_unified_tradeoff/run.py --rounds 1
```
正式运行不要加--rounds 1，也不要恢复旧实验目录。终端打印本实验out/时间戳，恢复示例：
```powershell
python experiments/exp043_unified_tradeoff/run.py --resume 'experiments/exp043_unified_tradeoff/out/实际时间戳'
```
如果最初使用--rounds 1，恢复时也保留该参数。源码指纹、参数、工作负载或训练集变化会拒绝恢复；运行期间不要修改共享brokerlab或本实验代码。旧实验的源码和结果均保留。

## 共同实验条件

|参数|固定值|
|---|---|
|分片|16|
|brokers|50|
|用户|200|
|每方法每轮评估用户CTX|40,000；每broker选样800笔|
|重复|3轮；同一轮各方法配对|
|路由种子|7、17、27|
|选样种子|42|
|每个子账户初始资金|45 ETH；每broker720ETH，总预算36,000ETH|
|PnP初始预算|同为每broker720ETH，仅初始分布按训练规划|
|名义输入速率|120 CTX/s|
|出块间隔|1秒|
|单块gas上限|30,000,000|
|每broker用户交易并发上限|24|
|原始数据|trace/ETH_cleaned.csv；金额0.01—10ETH；去重选样|
|训练/评估分离|12,000训练CTX与40,000评估CTX互不重叠|
|评估负载|前12,000笔自然分布；后28,000笔目的热点在11/3之间每1,600笔交替，确保源目的不同|
|完整性|所有用户CTX终态可追溯、无重复丢失、账本与链一致、全局守恒、burn/mint对账；不通过不纳入图|

训练集、金额、用户与分片映射、评估有序交易流由本次run统一构建，然后所有方法复用。同一轮各方法使用同route seed。每方法运行独立重启链，避免上一方法的资金状态或队列泄漏。运行串行，避免并行方法争用同一机器导致比较偏差。

## 方法和组件设计（14个点）

|方法名|调度|估计器|epsilon|q_min|
|---|---|---|---:|---:|
|Plain|不主动再平衡|不适用|—|—|
|Proportional + Hard window|比例重排|20块硬窗口|.95|.3|
|Proportional + EWMA|比例重排|EWMA半衰期20块|.95|.3|
|Top-up + Hard window|只补符合条件的缺口|20块硬窗口|.95|.3|
|Top-up + EWMA|只补符合条件的缺口|EWMA半衰期20块|.95|.3|
|Top-up + EWMA (epsilon=0)|同完整方案|同完整方案|0|.3|

前五组是Plain加2×2组件设计，第六组隔离死区作用。完整TDR只运行一次，名称明确为Top-up + EWMA。比较EWMA时固定调度策略；比较Top-up时固定估计器；比较死区时固定其它参数。

四个2×2组都使用相同q_min和epsilon，额外绝对reserve、目标cap、qfloor和lowwater等机制不启用。窗口/半衰期均20逻辑块；EWMA最少观测20块，硬窗口本身要积累完整20块，消除原5块/20块冷启动差异。EWMA不硬截断历史，two_sided=false。TDR组事件最小间隔10块、执行超时20块，同样的broker编号%5启动错峰。epsilon=.95沿用既有定义，不是少5%就补；q_min=.3为目标分配地板，不是实际余额保证。

其余八种：Flexichain、Loopmax、Spider-rebalance、PnP-static、Autoloop、Overpass-uniform、Overpass-demand、UCRB。

|方法|固定参数及边界|
|---|---|
|Flexichain/UCRB|当前支付不足时补缺、等待到账并重试；在该平台决策可能等价。等待纳入延迟/吞吐原始记录|
|Loopmax|参考容量90ETH、预测horizon20块、安全额2块|
|Spider-rebalance|每broker成功服务金额累计180ETH（初始资本720的.25倍）触发检查|
|PnP-static|12,000条独立训练、epoch10块、outage=.05、固定总预算720ETH；只规划一次初始化|
|Autoloop|参考容量90ETH、low=.3/high=.7，即27/63ETH，目标45ETH|
|Overpass-uniform/demand|检查10块，均匀/需求目标，零触发死区；默认单片QP上限720ETH，权重默认一致|

90ETH参考容量只属于需要通道容量的规则，不是所有账户的物理上限。外部方法保留自己的触发逻辑：执行代码对这些SOTA方法不施加TDR的10块chi；不能为了“统一”而把所有方法改成同一算法。均为分片平台上的决策规则适配，不是完整PCN/密码协议复现。

## profit=0的统一含义

横轴R：用户最终走relay的CTX数。纵轴A：成功在线再平衡逻辑CTX数，一次burn+mint合计一次。沿用你要求的纵轴标题TDR-triggered relay CTXs，但数据含义为再平衡请求数，不是TDR导致的用户relay回退。

固定事后经济模型：每笔新增broker服务收入kappa=.1成本单位，每笔再平衡成本mu=1成本单位。单次搬运需要10次新增服务覆盖。实际链上gasPrice=0，本包不把模型利润声称为实测扣费利润；没有计入资金机会成本与离线规划成本。

每一轮以该轮唯一的Plain为参照：DeltaP=.1*(R0-R)-A。全部方法共用一个协议，故统一边界A=.1*(R0-R)。图取三轮原始计数的均值，R0也取三轮Plain均值；线性利润与逐轮配对利润均值严格一致。误差棒为三轮最小—最大值，不是置信区间。线下模型增量利润正、线上负，不预设TDR一定在线下。不根据结果把费率或epsilon调到有利位置。

## 输出与重画

out/时间戳包含manifest.json、params.json、training.json、workload.csv、raw_results.csv、report.json，以及逐任务summary、ctx_rows、tdr_moves、日志。manifest记录协议版本、源码hash、训练/评估hash及完成任务状态。

跑完自动输出figs/unified_tradeoff.pdf、PNG、point_values.csv、per_round_values.csv、figure_info.json。图中一个Plain、一个Top-up+EWMA；无[SOTA]/[ablation]后缀，无40k/80k分组图例，无底部小字。宽177.94mm，标签10pt，轴标题11pt。SOTA圆点、组件对照菱形、完整方案红星；每个点直接标具体方法名。

若图的标签检查提示重叠，实验数据仍完整保存，不必重跑实验，只修复绘图后执行：
```powershell
python experiments/exp043_unified_tradeoff/plot.py --experiment-source 'experiments/exp043_unified_tradeoff/out/实际时间戳' --output 'experiments/exp043_unified_tradeoff/out/实际时间戳/figs'
```
分析档重画加--allow-analysis，图中明确标注ANALYSIS ONLY；正式数据校验缺少任何方法/轮次或来源不符会拒绝出图。旧实验结果不能混入。

## 可运行性验证

已运行--dry-run：42任务×40,000 CTX；真实preflight：40,000评估+12,000独立训练，PnP预算可行；12项新协议测试通过。未启动Anvil链、未产生新性能结果。现在可以由你在D盘自行启动正式实验。
