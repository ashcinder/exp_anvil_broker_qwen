# 实验19：Clean 数据集上的 EWMA 三参数单因素搜索

数据固定为 `trace/ETH_cleaned.csv`，排除合约端点，金额过滤为 0.01～10 ETH。
每个候选配置运行 10,000 CTX；固定 50 brokers、16 分片、150 ETH/分片、
120 CTX/逻辑块。一次只改变一个 EWMA 参数，其余保持基准值：观测窗口 5 块、
epsilon=0.95、半衰期 20 块、q_min=0.1、chi=10。

三组搜索：

- EWMA 观测/冷启动窗口：5、10、15、…、50 个不同块，共 10 个取值；
- deficit epsilon：0.05、0.10、0.15、…、1.00，共 20 个取值；
- EWMA 半衰期：1、2、3、…、50 块，共 50 个取值。

EWMA 没有传统有限滑动窗口；这里的“窗口长度”是策略开始规划前必须观察到的
不同块数。半衰期单独控制指数记忆长度。三个单因素搜索共有 80 个参数取值，
其中基准配置在三组内重复，去重后是 78 个 EWMA 候选。共同的四种对照臂
使用内容缓存，只运行一次，因此完整搜索预计执行 82 个 10,000 CTX 方案。

运行：

```powershell
python experiments/exp019_clean_ewma_parameter_search/run.py
```

预览计划但不启动链：

```powershell
python experiments/exp019_clean_ewma_parameter_search/run.py --dry-run
```

输出位于 `out/<timestamp>/`，包含原始结果、聚合 CSV、参数影响表、推荐值和图。
单个 route seed 的结果属于探索性搜索，不应直接作为最终统计结论。
之前中断的 `out/20260928_201315/` 使用旧搜索范围；运行新设计时请直接执行
上述命令创建新目录，不要用 `--resume` 恢复旧目录。
