# 实验042：EWMA多轮配对，一次绘制Fig1a、Fig1b和Fig5a

本实验是独立的新实验，不修改实验026/040/041，不启动余额/交易量扫描，不复用其单轮利润曲线充当多轮样本。

## 正式设计

默认10轮，每轮Plain和EWMA Topup各一次，共20次链运行、每次80,000笔用户CTX，总执行量1,600,000。奇数轮Plain先跑，偶数轮EWMA先跑，以降低固定执行顺序带来的系统偏差。两种模式串行启动独立Anvil分片链，同一轮使用相同输入与路由种子7、17、…、97。所有轮使用同一固定输入，是路由/执行重复，不是十份独立trace。

- 16分片、50broker、200用户，预先固定展示broker19。
- 每broker每分片45 ETH，每broker共720 ETH，全系统36,000 ETH。
- EWMA沿用实验026方案：Topup、epsilon=.95、q_min=.3、半衰期20块、最少5个观测块、chi=10块、超时20块、单侧Topup，无利润门槛。
- 固定完整用户手续费21,000,000 wei；broker成功服务留存10%=2,100,000 wei=0.0021 Gwei。
- 每笔确认burn的TDR搬款计21,000,000 wei=0.021 Gwei；按搬款逐笔收费，不按触发次数收费，burn/mint不重复收费。费用是事后模型，不从链上可用资金扣款；链gasPrice=0。
- 默认读取原实验026的冻结80k workload.csv，只读取、不改写。它包含第24k后、每3200笔在11/3热点轮换的构造负载。
- 主图显示前50k，全80k结果另外保留。不要把N改成50k：这会改变按比例定义的热点时刻。

## 一次导出的图

| 文件 | 内容 |
|---|---|
| figs/fig1.pdf | Fig1a、Fig1b左右组合 |
| figs/fig1a.pdf | OFF前500个submit_block的5个100-block堆叠柱；均值和标准差误差帽 |
| figs/fig1b.pdf | OFF broker19利润与共同理想上界，均值±标准差阴影 |
| figs/fig5a.pdf | EWMA利润、OFF利润、一个共同理想上界；前50k，三条曲线及阴影 |
| figs/fig5a_full.pdf | 同一数据的完整80k曲线 |
| figs/overleaf_fig1_fig5a.zip | 可替换figs/fig1.pdf、figs/fig5a.pdf的矢量PDF和图注 |

三条曲线先逐轮计算，再跨配对运行计算均值±样本标准差（ddof=1）；不是置信区间，不人为增宽阴影。灰线沿用当前三曲线图的共同理想包络：每轮、每个进度点取max(ON累计分配用户CTX数, OFF累计分配用户CTX数)×0.0021 Gwei，假设零再平衡费用。它是两个实际分配轨迹的理想参考上界，不是严格求解的可达最优利润。只汇总两模式都完整完成的配对轮次。

Fig1a仅展示前500个提交区块，未展示的尾部交易不合并进第5根柱子。误差帽分别由每轮relay数量和堆叠总数计算，不能把两部分标准差相加。

## 新增准确记账坐标

本实验的Transaction Index明确为**全系统用户交易完成序号**，不是旧图的arrival_pos到达序号。每个成功完成的用户CTX在跨进程共享锁保护下记录唯一accounting_index=1…N；原arrival_pos继续保存。

TDR burn成功回执被引擎观察到时，记录当时的全局完成序号、转账hash、分片、金额、burn区块和成本。净利润按该进度直接扣除，不再用区块投影、均匀摊销或完成数量插值推算。用户服务收入在该用户交易完成时确认；末尾清账期发生的TDR成本记在N。一个序号上可以有多笔成本，全部累计；N点包含尾部成本。

因此这次多轮均值与旧026单轮到达序号图不必逐点相同。新代码不声称重放历史运行的精确调度或使用原026旧版后端二进制；它固定输入、原EWMA参数和本次后端源码快照，并记录哈希。

为避免影响已有运行，每个新运行先在自己的out目录复制后端Python源文件，再仅在副本中注入观测记账hook。原brokerlab/pipeline不修改。后端布局不兼容时立即报错，不默默应用错误补丁。

## 如何运行

推荐在实验040结束后，把本文件夹放到完整D盘项目的experiments/exp042_ewma_figure_repeats。**不要在实验040运行期间把新Python文件加进其后端项目**：实验040可能把这识别为源码变更。本实验可直接从当前C盘文件夹运行，并只读指定D盘backend。

在完整项目根目录：

```powershell
# 只看20次任务计划，不起链
python experiments/exp042_ewma_figure_repeats/run.py --dry-run

# 单元、跨进程计数、记账与全部PDF的软件验证；不启动Anvil
python -m unittest discover -s experiments/exp042_ewma_figure_repeats -p 'test*.py' -v

# 小规模真实链验证：2分片、2broker、2轮、4个方案运行，共320笔CTX
python experiments/exp042_ewma_figure_repeats/run.py --smoke

# 正式长跑：20次运行、160万笔用户交易，结束后自动绘图
python experiments/exp042_ewma_figure_repeats/run.py

# 仅重新绘图，不重新运行实验
python experiments/exp042_ewma_figure_repeats/plot.py --run '实验输出的RUN_DIRECTORY绝对路径'

# 中断后续跑：使用相同配置和源码，跳过通过哈希验证的完成任务
python experiments/exp042_ewma_figure_repeats/run.py --resume '原RUN_DIRECTORY绝对路径'
```

运行将打印`RUN_DIRECTORY=...`。输出默认位于本实验目录out/时间戳。正式运行不要使用`--smoke`。`--rounds 3`可试跑3组配对，但会按3轮而非10轮标注，不视为默认正式10轮设计。

当前C盘代码直接运行方式，无需改写D盘后端：

```powershell
$exp042Path = 'C:\Users\hzy\Downloads\exp_anvil_broker_qwen\exp_anvil_broker_qwen\experiments\exp042_ewma_figure_repeats'
$exp042Backend = 'D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen'
python "$exp042Path\run.py" --backend-root "$exp042Backend" --dry-run
# 等实验040结束后：
python "$exp042Path\run.py" --backend-root "$exp042Backend" --smoke
python "$exp042Path\run.py" --backend-root "$exp042Backend"
```

默认与040/041共用45940执行锁。被占用时拒绝起链，不终止任何现有进程；不要改锁端口绕过串行限制。`--prepare-only`只准备后端副本及冻结输入，不启动链，可在已有运行期间使用。准备后的运行可使用`--resume`继续；smoke准备的目录续跑时也要带`--smoke`。

没有历史026冻结输入时，可显式使用`--fresh-workload`生成一份新的80k固定输入；它会记录为新工作负载，不能声称与历史026输入完全相同。正式10轮不要和旧026/040/041结果混合计算阴影。

## 核验及输出明细

每轮保存：effective.yaml、summary.json、原始ctx_rows.csv、tdr_moves.csv、accounting/broker_*_costs.csv、curve.csv、bins.csv、result.json。记录输入哈希、源码哈希、后端快照、各图统计CSV和配对轮数。

核验包括每笔输入身份与金额、无重复无丢失、accounting_index完整1…N、保存的链/账本守恒gates、真实留存手续费、每笔TDR burn与最终搬款明细一一对应、末尾累计费用与转移数一致。失败/丢失的搬款或守恒失败不会被当成有效绘图样本。

创建时已通过不启动链的dry-run、真实后端副本编译/导入与输入准备、5项软件测试（包括Windows spawn的跨进程完成序号以及三组图完整导出）。真实Anvil smoke被正在运行的实验040执行锁阻止，尚未通过链上smoke验收。未启动正式长跑。软件fixture均明确标为synthetic/smoke，不能用作论文数据。
