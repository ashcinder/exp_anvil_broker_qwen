# 实验34：独立下侧触发参数（v3）

策略名topup_lowwater。新增low_water_ratio（记作α），q_min恢复为目标分配地板参数；epsilon只作用于供款上侧。原始Topup、v1/v2的q-floor策略及历史结果保留，不能与v3恢复或合并。

## 算法

每个broker以可用余额β（已扣预留）和EWMA需求λ决策：

1. 计算总可用资金L与需求比例目标τ；q_min仅用于目标地板L×q_min/n，并守恒修正，使Στ=L。
2. 补款候选：β_i < α×τ_i，缺口为τ_i−β_i。
3. 供款候选：β_j > (1+epsilon)×τ_j，最多供款β_j−τ_j，供款后不低于自身τ_j。
4. 最大盈余与最大缺口匹配，补到τ；盈余不足时部分补款。保留事件互斥、χ间隔、burn/mint闭环及超时守恒记账。

下侧阈值与上侧边际使用1e6整数定点取整，金额为wei。严格小于触发，等于不触发。α∈[0,1]；α=0关闭下侧触发，α=1在任何目标缺口处补款。epsilon允许任意有限非负值，epsilon=16对应17τ的供款阈值。旧Topup仍保留其原epsilon范围。

三个角色：q_min决定“目标如何分配”，α决定“何时补款”，epsilon决定“何时允许供款”。独立是参数职责独立；q_min会改变τ，因此仍可能间接影响绝对触发金额。α不是固定ETH水位，也不承诺余额始终高于ατ，供款可用性和执行时延仍会影响补款。

例：总额100，需求1:4，q_min=.3，τ=[20,80]，实际β=[80,20]。α=.3时，下侧线为[6,24]，分片1的20<24；epsilon=.95时，供款阈值为[39,156]，分片0的80>39，因此搬60，余额变[20,80]。只改α=.2，则下侧线为[4,16]，20不低于16，无搬运。此前q_min下侧方案的线为15，此例也不会触发。

## 参数与敏感性

每参数点每轮40,000CTX，正式10轮，单因素扫描。在epsilon、半衰期和q_min扫描中默认low_water_ratio=.3；可通过--set exp.low_water_ratio=0.5统一修改。

| 扫描维度 | 网格 | 其余固定 |
|---|---|---|
| epsilon | .05,.1,.2,.3,.5,.7,1,1.5,2,4,8,16 | 半衰期20，q_min=.3，α=.3 |
| half_life | 1,2,4,6,8,10,15,20,25,30,35,40,45,50,100,150,200,250,300,400,500 | epsilon=.95，q_min=.3，α=.3 |
| q_min | .1,.2,.3,.4,.5,.6,.7,.8,.9 | epsilon=.95，半衰期10，α=.3 |
| low_water_ratio | .05,.1,.15,.2,.3,.4,.5,.6,.7,.8,.9,1 | epsilon=.95，半衰期20，q_min=.3 |

默认运行全部四组：54点×10轮=540次，21,600,000CTX。可用--only选择维度。仅epsilon为120次、4,800,000CTX；仅α也是120次、4,800,000CTX。low_water_space.yaml仍可单独使用。

公共参数：clean数据ETH_cleaned.csv；16分片、50broker、200用户；每broker每片45ETH（单broker720ETH）；全局120笔/块，1秒/块；金额筛选.01～10ETH，排除合约端点；候选提取200,000，发送方组上限平均大小2倍；无PnP训练或SOTA对照；10轮同批交易，路由种子7,17,27,37,47,57,67,77,87,97；数据种子42；χ=10块，搬运超时20块；至少5个不同需求观测块后启动；EWMA历史不截断。

方向旋钮开启：前30%保持原目的分片；后70%在11、3之间轮转，每1,600笔切换；强度1.0，避免同片CTX。clean交易经筛选并人工改写方向，非原生自然流量。

## 运行

从当前工作区根目录：

```powershell
python experiments/exp034_qmin_floor/run.py --dry-run
python experiments/exp034_qmin_floor/preflight.py
python experiments/exp034_qmin_floor/run.py
python experiments/exp034_qmin_floor/run.py --only epsilon
python experiments/exp034_qmin_floor/run.py --only epsilon --set 'exp.low_water_ratio=0.5'
# 只扫描独立下侧参数。
python experiments/exp034_qmin_floor/run.py --only low_water_ratio
python experiments/exp034_qmin_floor/run.py --smoke
python experiments/exp034_qmin_floor/run.py --only epsilon --rounds 1
python experiments/exp034_qmin_floor/run.py --only epsilon --resume '<首次run目录>'
```

当前目录是D盘项目的原生实验入口，不依赖C盘转发入口。保留实验34目录名以便现有入口继续使用，算法版本为exp034-independent-lowwater-v3。任务manifest、标签、逐点params与worker payload均显式记录low_water_ratio，恢复检查源码、负载与配置。图表输出均值和样本标准差，旧版本结果拒绝合并。完整正式实验尚未启动。

本次加密网格将用户输入015解释为0.15，末尾0.91按0.9和1两个点处理。按用户最新要求，epsilon=0已从实验网格删除；代码仍允许该数学边界用于单元测试。

代码审查修复：默认包含α扫描；所有epsilon点严格为正，可完整使用对数横轴；逐点exp参数快照与实际token统一；恢复与绘图核对alpha、epsilon、q_min、半衰期及引擎，不接收错配结果。历史版本的运行受源码指纹保护，不能直接续跑。

Windows路径修复：任务目录使用t/短编号/短时间戳，臂标签使用lw_短维度名，完整token与所有参数仍在manifest和逐点配置中。默认路径针对全部54点检查低于Windows路径长度限制。

D盘安装验证：从D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen运行，输出写入本目录out/。迁移时仅应用实验34的五个共享文件补丁并新增实验目录，D盘原有其他修改保留。
