# 实验41：Fig1专用TDR-off多轮实验

本实验只为Fig1收集关闭TDR的数据，与实验40的TDR-on收益扫描独立。每轮每子账户45ETH、40,000用户CTX，16分片、50broker、200用户，重复10轮，**共10次、400,000条用户CTX**。不扫描余额/交易数，不开启TDR、不运行TDR-on对照。

## Fig1左图的柱子与小帽子

每一轮都按原始 `submit_block` 分到相同的100块区间：[0,100)、[100,200)…。横坐标标区间右端点。所有轮次统一起点、统一宽度，并覆盖最长一轮的全部交易；较短轮次后续区间计0，不裁掉排队尾部、不把不同轮分别缩放到0–500。

先逐轮统计每个区间的broker CTX和relay CTX，再跨轮计算均值及**样本标准差**。柱子是平均broker数量（蓝色底部）与平均relay数量（橙色顶部）的堆叠，纵轴在左边；没有通信量红线及右纵轴。

小帽子分别画在broker/relay分界处和堆叠柱顶：前者是broker数量的跨轮标准差，后者是逐轮总数的标准差。总数的标准差直接从每轮总数算，不把两部分标准差相加。若某区间总数恰好不变，顶端误差自然为0，分界处仍可显示路由份额变化。单轮数据不伪造误差棒。

输出PNG/PDF及 `fig1a_bins_by_round.csv`、`fig1a_bin_statistics.csv`、`fig1a_metadata.json`，保留每轮种子和区间计数。区块坐标沿用旧Fig1的submission-height口径，不声称不同分片拥有严格同步的墙钟。

## 参数与费用

每轮route_seed依次为7、17、…、97；选流seed42。和实验40采用同一200k母工作流设计，选出后取前40k；目的热点从第12,000笔开始、每1,600笔在11/3轮换。rate=120、1秒/块、sender_window=2。该组40k基准与实验40的40k基准应具有相同工作流哈希。

TDR实际引擎为plain，落盘 `tdr.enabled=false`，每轮搬运次数必须为0，否则校验失败。保持固定手续费机制开启：F=21,000,000wei，broker成功服务收入0.1F=2,100,000wei。relay对应的指派仍进入该broker潜在收入上限，内部搬款不计。

因此这10次还会导出Fig1右图的未开启TDR收益与上限曲线，阴影为跨整轮均值±样本标准差，无须额外实验。

## 运行

从C盘工程根目录执行：

```powershell
# 查看10次off-only计划
python experiments/exp041_fig1_plain_repeats/run.py --dry-run

# 小规模验证：2分片2broker，40CTX×2轮=80CTX；用于检查误差帽，不是正式数据
python experiments/exp041_fig1_plain_repeats/run.py --smoke

# 准备真实工作流，不执行十轮
python experiments/exp041_fig1_plain_repeats/run.py --prepare-only --no-plot

# 正式10轮；不会启动170次扫描
python experiments/exp041_fig1_plain_repeats/run.py

# 暂停在首轮后，后续续跑
python experiments/exp041_fig1_plain_repeats/run.py --max-tasks 1 --no-plot
python experiments/exp041_fig1_plain_repeats/run.py --resume '本实验运行目录'

# 完整结果单独重绘；不完整探索数据需追加--allow-partial
python experiments/exp041_fig1_plain_repeats/analyze.py --source '本实验运行目录'
```

独立配置和输出均在本目录，执行入口共享相邻实验40的链执行/记账代码。迁移时同时保留两个目录，以及完整exp025后端。本机后端自动使用D盘完整工程；也可传 `--backend-root`。

两实验共用运行锁，必须串行；各自有独立manifest、任务状态、失败重试记录。续跑保留首次--smoke/--rounds/--set，配置或源码变更请开新运行。旧340次配对计划的manifest不接入这两个新实验。

主要产物：`figs/fig1a_tdr_off.png/.pdf`，仅包含broker/relay柱子及真实重复误差；`fig1b_broker_19.*`是未开启TDR的单broker收益/潜在上限曲线，另有system汇总版。所有逐笔CTX与费用、守恒检查记录均保留。

本次按用户要求仅交付代码与小规模验证，不启动正式10轮。
