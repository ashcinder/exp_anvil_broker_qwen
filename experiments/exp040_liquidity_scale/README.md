# 实验40：TDR_ON 资金与交易规模参数敏感性

只开启Topup+EWMA，研究broker赚多少钱，为Fig5b/c生成金额箱线图。没有TDR_OFF对照任务；OFF的Motivation和收益阴影图在独立实验41。

| 扫描 | 取值 | 固定条件 | 运行次数 |
|---|---|---|---|
| Fig5b初始余额 | 每broker每分片子账户15、30、45、75、100、150、200、300、500、1000 ETH | 40k用户CTX、TDR on | 10点×10轮=100 |
| Fig5c交易总数 | 10k、20k、40k、60k、80k、100k、150k、200k | 每子账户45ETH、TDR on | 8点×10轮=80 |
| 共享基准 | 45ETH/40k | 同时进入两张图 | 去重10 |

合计17种配置×10轮=170次，共10,200,000用户CTX。不是资金×规模完整交叉网格。轮次在外层，格子轮转，route_seed=7、17、…、97。每个箱子的样本是一整轮，不能把50个broker当作50次独立重复。

## 共同参数和收益

16分片、50broker、200用户、1秒/块、释放120CTX/块、sender_window=2。45ETH表示每子账户初始资金，单broker合计720ETH、全系统36,000ETH；1000ETH档相应为16,000和800,000ETH。

默认使用仓库已提交的原始Topup：epsilon=.95、EWMA半衰期20块、q_min=.3、chi=10块、至少5个观测块预热、无限历史EWMA、单侧触发、搬运超时20块。本实验不切换本地未提交的独立alpha策略，也不修改共享算法。

固定手续费F=21,000,000wei，成功服务一笔实际留存0.1F=2,100,000wei。每broker潜在收入为其全部实际指派用户CTX（包含relay）数×0.1F；内部TDR不增加上限。净收益是实际手续费收入减去每次完成转移21,000,000wei的名义成本。一次burn+mint计一次转移；该成本不回扣余额，Anvil实际gasPrice=0。

Fig5b/c主图纵轴为净收益金额（以F为单位），另输出手续费收入金额版本。CSV保留精确wei、收益兑现率、成本、交易/搬运数；预先固定代表broker19，并保存全部broker和系统总额。系统总额不冒充单broker收益。

真实ETH_cleaned筛选金额.01～10ETH、排除合约端点；候选从400k扩至最多3.2m，目标不足时失败。统一先选200k母工作流，各N取前缀，第12,000笔后每1,600笔在目的热点11/3间轮换、避免同片。因此10k尚未进入热点阶段；改变N是在固定速率/热点节奏下增加运行长度。

实验41采用相同母流设计。相同输入/种子不保证动态匹配到同编号broker的交易集合相同；各臂按自己的实际指派计算上限。不同轮次是固定trace下的路由与执行重复，不代表跨独立负载的泛化。

## 运行

在仓库根目录安装requirements.txt和Foundry/anvil，并准备trace/ETH_cleaned.csv：

```powershell
python experiments/exp040_liquidity_scale/run.py --dry-run
python experiments/exp040_liquidity_scale/run.py --smoke
python experiments/exp040_liquidity_scale/run.py
```

smoke为2分片2broker、3次/160CTX，标SMOKE。--prepare-only --no-plot只选流；--max-tasks 2 --no-plot执行原计划前两次后暂停，仍保留完整十轮计划。资源上限为backlog240k、排空预算7200秒、RSS12GB，超限记失败，不伪装成正常结果或实测耗时。

```powershell
python experiments/exp040_liquidity_scale/run.py --only balance
python experiments/exp040_liquidity_scale/run.py --only transactions
python experiments/exp040_liquidity_scale/run.py --resume '本实验完整运行目录'
python experiments/exp040_liquidity_scale/analyze.py --source '本实验完整运行目录'
python -m pytest experiments/exp040_liquidity_scale/test_design.py -q
```

仅余额100次，仅规模80次；分开运行时共同基准各执行一次。续跑保留首次--only/--rounds/--set/--smoke；数据、配置和源码指纹变更拒绝续跑，重试另开attempt不覆盖失败数据。--rounds可生成标EXPLORATORY的减少轮次计划。

产物在本目录out/<时间戳>/：独立manifest/report、冻结工作流哈希、每任务有效配置、CTX/TDR台账、每broker收入及净收益、figs/fig5b_broker_19.*、fig5c_broker_19.*，以及_fee_income和system版本。箱体Q1–Q3、中位线、1.5IQR须及逐轮散点，标实际n。基准10轮可复用Fig5a收入/潜在上限均值±样本SD曲线，不增加实验。

两实验共享45940端口锁，必须串行执行；实验41仍运行时，本实验拒绝启动。完整源码与当前工作流都来自本仓库，不需要作者机器上的C/D盘副本。可用--backend-root选择另一份完整兼容工程，默认使用当前仓库。

正式运行共170次；实验41另10次。上传不代表已经执行这180次。
