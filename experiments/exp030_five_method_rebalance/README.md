# 实验30：五种钱包内资金策略对比

**当前为设计v2：主指标是Relay＋资金调整CTX，以及broker净利润。先读 [参数审阅与自行运行说明](DESIGN_REVIEW_CN.md)。本轮没有运行新的链上实验。**

本实验独立创建在 D 盘原项目内，参考 `submit_regime_shift`，复用实验25/26已修复的执行管线。
不覆盖旧实验、旧结果和共享 brokerlab。入口为本目录 `run.py`。

已完成更换OPRE后的两轮预实验，见 [最新有效结果](LATEST_PILOT.md)。正式规模尚未执行。

## 先学习，再判断可写入论文的范围

先读 [论文概览](docs/00_四篇论文与实验总览.md)，再依次读四篇学习笔记。
**这五臂是同一分片钱包环境中的适配策略比较，不是四篇论文完整协议的复现。**
OPRE 已按用户要求排除。第四臂换成 ICML 2024 的 LAI(1)，正式会议全文已保存，Algorithm 2 的连续决策公式已核验并直接实现。
`report.json` 仍记录 `paper_algorithm_reproduction_complete=false`：论文原问题是连续二次优化，本项目另有需求映射、整数金额及延迟链上执行适配。
更换前批次仅保留作审计，不混入更换后的五方法结果。

| 代码名 | 实际方法 | 复现边界 |
|---|---|---|
| brokerchain | 当前 broker 两阶段交易，无主动再平衡 | BrokerChain核心机制基线；不复现完整期刊系统的账户划分/共识 |
| horcrux | 成功 broker 交易产生源→目的补偿需求，反向需求抵消，定期聚合调拨 | flow-neutrality思想适配；资金已确认后才可使用；没有虚拟通道、即时调拨 |
| shaduf | 每对子账户全绑定 AB；按已观察交易需求补足目的资金；优先使用绑定内可搬资金最多的来源 | 抽取Shaduf实验分配规则；不复现Shaduf++隐私/安全协议 |
| lai | LAI(1)：上次连续分配与当前需求比例插值，按确认资金总量生成整数目标，再搬移盈余 | 原论文连续规则可核验；需求映射、5%均匀底仓、整数舍入、链上延迟为项目适配 |
| tdr | Top-up + 无限记忆EWMA，hl=20、q_min=0.3、epsilon=0.95 | 沿用25/26策略，修正执行/记录问题；不是重新设计TDR |

## 实验设置

默认正式规模：16分片、50 broker、每子账户45 ETH、每方法40000个唯一CTX、3整轮。
同一轮5方法共用选流、金额、人工热点轮换和route seed；每方法重启独立Anvil链，串行运行。
轮次route seed为7/17/27，运行顺序按轮次轮换。总计15次运行、600000个用户CTX。
路线种子相同不保证方法实际分派相同：余额变化会改变可选broker，这是被测行为。
冷却10逻辑块、同一broker最多一个活跃事件、最多n-1个转移；每逻辑块最多决策一次。
所有方法走实际签名burn/mint，不能直接修改运行期余额，也不能使用外部补资。
coordinator铸造预算是模拟执行资源，不是broker策略的额外资本；预算耗尽判运行无效。

**热点变化为人工构造。** 基础金额来自ETH_cleaned，不重放主网合约或真实时间变化。
这套数据支持方向切换场景的适配实验，不支持无条件声称所有网络/所有负载中最好。

## 修复记录

继承25/26：回执轮询饥饿、move_events接口、同分片热点碰撞、收口FIFO屏障、路由预留不被刷新抹掉。
新增：路由器在派发时只预留目的支出，不提前增加未确认源收入；成功报告后才增加源收入和留存费。
这是一项保守路由修复：源收入等完整CTX确认后才可被路由器使用；本地账本仍以真实阶段回执入账。
保留本机RPC时序影响：逻辑块释放相同不等于并发执行轨迹完全确定，多整轮重复用于报告波动。
`upstream_manifest.json`记录来源文件、SHA256和修复清单；`manifest.json`记录代码/配置/负载指纹。

## 运行

使用原项目Python环境和PATH中的anvil；从项目根目录运行：

```powershell
python experiments/exp030_five_method_rebalance/run.py --dry-run
python -m pytest experiments/exp030_five_method_rebalance/test_comparison.py -q
python experiments/exp030_five_method_rebalance/run.py --smoke
python experiments/exp030_five_method_rebalance/run.py --pilot
python experiments/exp030_five_method_rebalance/run.py
```

pilot是16分片、4 broker、每方法1000 CTX、2整轮；smoke为1 broker、80 CTX、1整轮。
两者都标记pilot，不得替代正式规模数据。默认完整规模尚需等待实际执行完毕才有结果。
续跑原目录：`python experiments/exp030_five_method_rebalance/run.py --pilot --resume <目录>`。
续跑必须使用第一次相同参数；源代码、配置和负载变化时拒绝续跑。
失败任务保留错误与原件，不纳入有效汇总；完成任务重新校验。

## 输出与计量

| 文件 | 内容 |
|---|---|
| RESULTS_CN.md | 五方法逐轮结果表，含复现限制 |
| primary_metrics.csv / primary_summary.csv | 主指标：Relay＋资金调整CTX、总/平均broker利润；逐轮数据和整轮统计 |
| design.json | 本批实际生效的共同参数、方法参数与费用参数 |
| raw_results.csv | Relay、Broker CTX、事件数、调拨转移数、事件参考CTX、收入/成本/利润、gas、延迟、吞吐、决策耗时 |
| method_summary.csv | 每方法按整轮聚合的均值/中位数/标准差/极值/95% t区间；单轮不报区间 |
| paired_vs_brokerchain.csv / paired_summary.csv | 同轮相比无调拨的增量服务、减少Relay、利润增量与盈亏平衡费用 |
| cost_sensitivity.csv | 不改变执行轨迹的事后费用扫描；不是费用反馈下重跑的实验 |
| phase_metrics.csv / destination_shard_metrics.csv | 后处理导出的自然前缀/热点切换阶段与目的分片的Relay及服务分解 |
| economics_by_block.csv / feedback_metrics.csv | 收入/费用逐块累计和同broker反向调拨统计；反向调拨不必然有害 |
| balance_metrics.csv / DETAILED_RESULTS_CN.md | 终态余额CV、最低子账户余额和包含延迟/gas/阶段分解的中文总表；CV与需求贴合度需区分 |
| comparison.png | 五方法Relay、调拨次数、收入、名义净利润；整轮均值和样本标准差 |
| tasks/*/*/ctx_rows.csv | 每个用户CTX的分片、金额、路由、真实确认块和延迟 |
| tdr_moves.csv | 每个成功调拨转移的真实burn/mint块号；所有方法使用同一字段名 |
| transactions.csv | 所有签名链上腿的哈希、种类、分片、回执status、真实gasUsed/effectiveGasPrice和字节数 |
| rebalance_decisions.csv / event_ctx_links.csv | 调拨决策与可审计的事件参考交易关联 |
| broker_metrics.csv | 每个broker的服务/Relay/转移/利润及终态余额CV；不能当作独立整轮样本 |
| capacity_by_block.csv | 每分片每个有交易块的链上腿数、gas使用率和调拨腿数 |
| balance_snapshots.csv / balance_by_block.csv | 钱包余额/目标快照和按真实确认块重建的聚合余额 |

“TDR触发的CTX”分成两列：`rebalance_transfers`是调拨跨片转移数（每笔有2个链上腿）；
`trigger_reference_ctx`是上次事件后已观察并归档到本事件的**用户**CTX数。
后一列不是严格因果触发数，不能把每个引用交易说成独立触发一次。
对非TDR方法也用统一的“调拨事件/转移”口径；无调拨基线两项均为0。

金额计算使用整数wei。B2E留存费是真实转账结果；Anvil gasPrice=0。
沿用实验26，名义调拨成本默认为每个确认burn腿21000000 wei，在链外结算，不从可用余额扣除。
broker名义净利润 = 已确认服务留存费 - 名义调拨成本。用户手续费烧币另列，不能再从broker收入重复扣除。
`actual_chain_gas_cost_wei`按实际回执计算，应为0；`rebalance_gas_used`是实际容量开销，不能写成0。
正式真实费用反馈实验仍需让执行层付gas并留出费用预算；本实验没有伪称完成这一部分。

所有任务完成后运行后处理：

```powershell
python experiments/exp030_five_method_rebalance/analysis/analyze.py <完整运行目录>
```

收入时序在用户CTX完成块入账，名义成本在真实burn块入账，终值与全局账单核对。
两者识别时机不同于即时链上余额；没有伪造墙钟时间或均摊费用。

## 完整性门

每个CTX唯一且金额/分片对应原负载；全部完成；broker账本=链；全局资金守恒；burn台账守恒；
每个链上腿都有成功回执；回执数量=普通CTX腿+调拨腿；每个CTX恰好被观测一次；
事件引用不能使用未来块信息或重复计数；每broker收入/名义利润之和=全局口径。
所有门通过后才标complete。实验失败不能替换为模拟成功或自动剔除后声称整批完成。
