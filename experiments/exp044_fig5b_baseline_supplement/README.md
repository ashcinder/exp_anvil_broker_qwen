# Fig5b交易量敏感性的两个补充方案

只新增Autoloop和Spider-rebalance，不重新执行With TDR或人为生成潜在收益。
默认复用D盘实验040的80次完整交易量结果、config.json和保存的8份工作负载。

## 参数

16分片，50 broker，200用户，每子账户45 ETH，提交速率120 CTX/s。
交易量10k/20k/40k/60k/80k/100k/150k/200k；每档10轮，种子7到97、步长10。
共160次运行，13,200,000笔用户CTX。轮次和交易量顺序沿用原实验；两个方案在轮次间交换先后顺序。
固定服务费21,000,000 wei，broker分成10%；每个确认再平衡transfer成本21,000,000 wei。
净利润=served用户CTX数*2,100,000 - 确认搬款数*21,000,000 wei。
统计broker 19全部16个子账户，保存所有broker的明细。

策略参数使用现有pipeline默认值：Autoloop low/high=0.3/0.7（按90 ETH容量计，边界27/63 ETH，目标45 ETH），检查周期10 blocks；Spider volume multiplier=0.25（180 ETH，基于broker全部16个子账户的累计流水）。capacity multiplier=2（90 ETH/子账户）；confirmation=10 blocks；SOTA chi=0，无TDR冷启动错峰。Spider按流水阈值触发，不采用Autoloop的10块周期。
EWMA半衰期20、预热5块等公共输入沿用exp040；并非强制两种SOTA采用EWMA。
未加载额外训练流（沿用exp040 sota_training_ctx=0）。属于仓库对原方案的适配实现，不声称完整复现论文原系统。
相同输入、配置与费用模型不保证实际动态broker分配相同；不同方案的潜在上界应使用各自assigned数。
原With TDR潜在上界可以作为参考，但不应伪称所有方案拥有完全相同的broker 19分配。

## 执行

在本C盘项目根目录：

```powershell
python experiments/exp044_fig5b_baseline_supplement/run.py --dry-run
python experiments/exp044_fig5b_baseline_supplement/run.py
```

再次运行同一命令自动校验并续跑；完成项跳过，中断项追加attempt，原始记录不覆盖。
默认结果写入本实验out/matched_transactions，实时进度.md每30秒更新（监督线程存活时）。
`--max-tasks 2`仅执行两个新的完整任务，不改变配置，之后可直接续跑。
此处不会修改D盘原实验目录，也不会触发其他实验对D盘源码的校验。
完成160次后自动生成fig5b_comparison_draft.png和broker19_comparison.csv。
也可用python experiments/exp044_fig5b_baseline_supplement/analyze.py查看已完成补充结果。
