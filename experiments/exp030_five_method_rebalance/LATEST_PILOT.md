# 最新有效预实验

OPRE 已替换为 LAI(1)。有效结果目录：`out/20261010_083830_034170`。

16分片、4 broker、每子账户5 ETH，每方法1000个用户CTX，2整轮，共10次独立运行、10000个用户CTX。18项测试通过，10次运行的完整性门均通过；全部用户交易完成，无失败CTX。源代码指纹检查通过；逐块经济累计与最终账单核对通过。

- [中文详细总表](out/20261010_083830_034170/DETAILED_RESULTS_CN.md)
- [逐轮结果](out/20261010_083830_034170/RESULTS_CN.md)
- [对比图](out/20261010_083830_034170/comparison.png)
- [费用敏感性](out/20261010_083830_034170/cost_sensitivity.csv)
- [论文概览](docs/00_四篇论文与实验总览.md)
- [LAI公式核验](docs/04_LAI_学习与公式核验.md)

LAI的两轮均值Relay=195、调拨=712.5；TDR的Relay=217、调拨=167。LAI服务量更大，TDR调拨开销更低。
当前模型每确认burn计2100万wei，全部主动调拨臂模型净利润为负，无调拨臂为正。真实Anvil gasPrice为0，模型费用不是实际链上gas费。
TDR相对无调拨的盈亏平衡单次费用约455万/538万wei，见配对结果；低费用结论来自事后扫描，不是费用反馈下重跑。

这些是钱包适配策略的预实验，不是论文原协议的完整复现。Shaduf++终版新增分配细节尚未完全核验；Horcrux虚拟通道及Shaduf++隐私协议未实现。

默认正式规模配置为16分片、50 broker、每方法40000 CTX、3整轮，尚未执行。入口已就绪，从项目根目录运行：

```powershell
python experiments/exp030_five_method_rebalance/run.py
```

早期含OPRE的批次及发现规划bug的批次分别标记SUPERSEDED或INVALIDATED，不纳入上述结果。
