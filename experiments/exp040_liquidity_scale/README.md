# 实验40：只开启TDR的资金与规模收益扫描（v2）

按用户最新要求，Fig5b/c均只研究TDR开启后的broker收入，不再运行Plain对照。关闭TDR的Fig1多轮实验独立放在相邻 `exp041_fig1_plain_repeats`。旧340次配对方案已停止使用；旧输出保留，不能用旧manifest续跑新方案。

## 需要做的实验

| 扫描 | 取值 | 固定条件 | 次数 |
|---|---|---|---|
| Fig5b，初始余额 | 每broker每分片子账户15、30、45、75、100、150、200、300、500、1000 ETH | 40,000用户CTX，TDR on | 10档×10轮=100次 |
| Fig5c，交易总数 | 10k、20k、40k、60k、80k、100k、150k、200k | 每子账户45ETH，TDR on | 8档×10轮=80次 |
| 共享基准 | 45ETH/40k | 同时属于两张图 | 减去重复10次 |

**合计170次、10,200,000条用户CTX，所有任务均为Topup+EWMA，无TDR-off任务。** 不做余额×规模完整交叉网格。

轮次在外层，资金/规模格子轮转执行；每轮路由种子7、17、…、97。同一条真实trace选流下的路由/执行重复，不是10份独立交易数据集。每个箱子的样本是10个整轮实验；50个broker不充当50轮重复。

## 赚多少钱的口径

固定单笔手续费 `F=21000×1000=21,000,000 wei`；broker成功服务一笔真实留存 `0.1F=2,100,000 wei`。收入与用户本金无关。

- `income_wei`：成功服务笔数×0.1F，并逐笔核对真实留存费用。
- `upper_wei`：分配给该broker的全部用户交易笔数×0.1F，包含后来走relay的交易。
- `modeled_cost_wei`：完成的TDR内部转移数×21,000,000 wei；一次burn+mint计一次转移。
- `net_profit_wei`：手续费收入−名义搬款成本。名义成本不从可用余额扣除；Anvil实际gasPrice=0。

Fig5b/c主图直接展示 **broker净收益金额**（以固定手续费F为单位），另导出手续费收入金额的箱线图。收益兑现率仍在CSV保存，不再替代“赚多少钱”作为主纵轴。默认预先固定broker19；全部50个broker明细及全系统汇总均保存。没有指派交易的兑现率为空。

## 公共参数

16分片、50broker、200用户；1秒/块，释放速率120笔/块；sender_window=2。45ETH表示每个分片子账户余额，一个broker总初始资本720ETH、全系统36,000ETH；1000ETH档相应为16,000ETH和800,000ETH。

默认沿用旧图的Topup：epsilon=.95、EWMA半衰期20块、q_min=.3、chi=10块、预热至少5个观测块、无限历史EWMA、单侧Topup、搬运超时20块。它不是exp034独立下侧阈值v3；需要v3时可开新运行并指定 `--set exp.tdr_policy=topup_lowwater --set exp.low_water_ratio=0.3`，不同算法结果不得合并。

ETH_cleaned金额筛选.01～10ETH，排除合约端点；候选从400k扩大，最多3.2m。不够目标交易数时失败，不暗中缩小N。先构建200k母工作流，再取各N前缀；第12,000笔后，每1,600笔在目的热点11/3轮换，并避免同片CTX。10k前缀尚未进入热点改写，这是一组固定热点时间尺度、增加运行长度的实验。

实验41也按同样的200k母流设计独立选流并取40k前缀，两实验基准工作流应具有相同哈希。不会把实验41的off任务混入实验40。动态路由仍开启，同编号broker在两实验中未必接到同一批交易；各实验用自己的实际指派计算单broker上限。

max_backlog=240k、排空预算7200秒、RSS上限12GB。它们是资源上限，不是预计耗时。每个任务启动独立分片链，串行执行；与实验41共用执行锁，避免同机相互干扰。

## 运行

从C盘项目根目录执行：

```powershell
# 查看170次计划，不起链
python experiments/exp040_liquidity_scale/run.py --dry-run

# 小规模TDR-on管线验证；2分片2broker，完整模式3次/160CTX
python experiments/exp040_liquidity_scale/run.py --smoke

# 只构建正式母流/前缀，不执行170次任务
python experiments/exp040_liquidity_scale/run.py --prepare-only --no-plot

# 正式170次；不会执行TDR-off
python experiments/exp040_liquidity_scale/run.py

# 也可以只做余额100次，或规模80次；分开运行时共享基准会各跑一遍
python experiments/exp040_liquidity_scale/run.py --only balance
python experiments/exp040_liquidity_scale/run.py --only transactions

# 先执行原计划的前两个任务后停下，不改变十轮计划
python experiments/exp040_liquidity_scale/run.py --max-tasks 2 --no-plot

# 续跑本实验目录；保留首次的--only/--rounds/--set/--smoke选项
python experiments/exp040_liquidity_scale/run.py --resume '本实验完整运行目录'

# 完成后单独重绘；不完整时须显式--allow-partial，图将标PARTIAL
python experiments/exp040_liquidity_scale/analyze.py --source '本实验完整运行目录'

# 设计/记账/误差帽测试（同时覆盖实验41）
python -m pytest experiments/exp040_liquidity_scale/test_design.py -q
```

本包位于C盘工程；运行自动选择完整exp025后端，目前为D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen。可用 `--backend-root` 指定完整后端。依赖Python、PyYAML、Web3、psutil、NumPy、Matplotlib、Foundry/anvil。

## 输出

每个独立实验有自己的 `config.yaml`、`run.py`、`analyze.py`、`out/<时间戳>/manifest.json` 和 `report.json`。共享链执行与记账实现以避免重复代码；没有共享任务状态。

- `results.csv`：每任务全系统收入、潜在收入、成本、净收益、服务/relay/搬运数。
- `per_broker.csv`：每任务每broker的金额明细，含broker19。
- `t/编号/a重试号/`：有效参数、summary、原始CTX/TDR台账、费用曲线、日志；重试不覆盖失败attempt。
- `w/`：冻结工作流前缀及哈希；源码、配置、trace及原始/派生数据更改拒绝续跑。
- `figs/fig5b_broker_19.png/.pdf`、`fig5c_broker_19.png/.pdf`：净收益箱线主图。
- `figs/fig5b_broker_19_fee_income.*`、`fig5c_broker_19_fee_income.*`：手续费收入箱线图。
- 相应的`system`版本是全体broker总额，不能当单broker收益。
- 基准十轮还可复用输出 `fig5a_broker_19.*` 收入与理论上限曲线；阴影为整轮均值±样本标准差。

箱体为Q1–Q3，中位线，1.5IQR须，叠加逐轮散点并标有效n。未完成正式计划时不自动输出正式图；SMOKE/探索图明确标注。正式实验仅在用户要求后启动。


## Fig5c additional baselines (2026-10-22)

Read [sota_supplement/README_CN.md](sota_supplement/README_CN.md). The independent entry point `sota_supplement/run.py` replays the saved 40k workload and runs only AutoLoop / Spider-rebalance, reusing completed TDR results. Default: 10 balances x 10 rounds x 2 new methods = 200 new tasks. Original runner and original results are unchanged.
