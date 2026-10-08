# 实验26：Fig1、Fig5a、Fig5c、Fig5d 的可绘图数据

同一份80000笔人工热点轮换负载，配对运行 Plain 与 Top-up+EWMA，各跑1轮。
使用16分片、50 broker、每个broker每个分片45 ETH，hl=20、q_min=0.3、epsilon=0.95。
两臂使用同一 route seed=7，各启动独立链。
共2次方案运行，160000笔用户CTX。50 broker × ctx_per_broker=1600，即每臂80000笔。
复用实验25的修复版蓝本管线；实验25仍保持每个参数点每轮40000笔。

8万笔选流：以200000笔合格候选为起点，不足时扩大到最多1600000笔候选（若文件耗尽则提前停止）。
先采用原来的发送者组大小过滤；仍不足8万时，按组大小升序补入被该过滤排除的组，直到选足。
不重复交易，不改变金额/成功状态/合约过滤条件。两臂使用完全相同的选流结果。
`workload_selection.json`记录候选池、去重数、实际扫描上限及补入组大小。
这项补足策略会改变旧蓝本的负载筛选口径，不改变TDR算法；实验25默认不启用。
若全部数据仍不足80000笔唯一合格交易，则报错并说明候选数，不生成虚构数据。

| 图 | 导出的数据 | 绘图口径 |
|---|---|---|
| Fig1 | Plain逐笔记录、`balance_by_block.csv` | 路由数量、CTX通信量、确认块上的分片聚合余额重建 |
| Fig5a | EWMA逐笔记录、带burn/mint块号的`tdr_moves.csv`、余额快照 | 保留之前的两类柱，橙色=Relay+成功TDR转移；TDR按真实mint块计数 |
| Fig5c | 两臂`profit_by_tx.csv`、`profit_metadata.json`、真实留存手续费 | broker19收入减名义TDR成本；不是Anvil实际gas净利润 |
| Fig5d | 两臂逐笔分片、路由、确认块 | 保留前684秒、9列的原热图样式，无交易格为灰色 |

两臂均开启B2E，费基数21000×1000 wei，broker留存10%。费用通过链上交易记账。
每次broker自签TDR burn的名义成本默认21000000 wei，在YAML里由
`exp.profit_tdr_cost_wei`设定；设为0则图中显示实际留存费用累计额。
Anvil gasPrice=0，这个成本是明确声明的经济模型，不是链上实际扣除的gas。
成本使用真实burn块，投影到当时已确认的最大arrival位置；尾部成本计入最后一点，
不再均匀分摊。所有收入、成本与终值核对。

通信量使用与修改后绘图代码一致的口径：broker为明确配置的100字节控制消息；
Relay为实际签名burn交易RLP字节加receipt JSON字节。红线只计用户CTX通信，
不包含未记录的TDR通信字节；该值不是物理网络带宽测量。
分片余额按真实Theta1/Theta2与TDR确认块重建，净留存费用在Theta1块入账，
遵循引擎账本语义，不表示费用烧币腿确认前的瞬时链上余额。

在仓库根目录运行：

运行环境沿用原项目：安装 `requirements.txt` 中的依赖；`anvil.exe` 在PATH里，
或配置 `chain.anvil_bin`；保留 `trace/ETH_cleaned.csv`。
仓库根目录是 `D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen`。
`exp.arms`里的方案参数覆盖基础TDR配置：`topup@45@@0.95@@20@0.3`
表示每分片45 ETH、epsilon=0.95、半衰期20块、q_min=0.3；空项采用管线默认值。

```powershell
python experiments/exp026_regime_shift_paper_figures/run.py --dry-run
python experiments/exp026_regime_shift_paper_figures/run.py
python experiments/exp026_regime_shift_paper_figures/run.py --resume experiments/exp026_regime_shift_paper_figures/out/<运行目录>
```

输出包含配置、任务状态、选流、逐轮原始CSV、摘要、报告及链日志。
所有轮次完成并通过守恒校验后，自动调用之前修改的绘图代码；时间轨迹固定使用第一轮，
两臂完整数据全部保留在报告中。目录适配和来源验证由`plot_regime_experiments.py`完成。

```powershell
python "D:\实验\exp8_latest_plotting\run.py" --experiment-source "<完整运行目录>"
python "D:\实验\exp8_latest_plotting\plot_exp8_latest.py" --experiment-source "<完整运行目录>" --figure fig5c
```

图的版式、颜色、字体、分箱和标注继续使用已修改的绘图函数。
旧实验8的入口仍可用；原绘图脚本修改前的备份保存在绘图目录
`backups/exp025_exp026_adapter/`。输出包括四图PNG/PDF和`source_manifest.json`。

`--smoke`为80笔、1broker、1轮检查（收益broker改为0），不属于论文正式结果。
基础源为`ETH_cleaned.csv`；热点轮换是人工构造，不能作为真实主网突变的证据。
热点等于源分片时，确定性地选择轮换表中下一个不同分片，确保每臂80000笔均为跨分片交易。
这是对旧蓝本负载改写的修正，因此不保证复现历史归档中的数值。
