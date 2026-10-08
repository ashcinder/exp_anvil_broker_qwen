# 实验19：Clean 数据集上的 EWMA 三参数单因素搜索

数据固定为 `trace/ETH_cleaned.csv`，排除合约端点，金额过滤为 0.01～10 ETH。
每个候选配置运行 10,000 CTX；固定 50 brokers、16 分片、150 ETH/分片、
120 CTX/逻辑块。每个配置重复 10 轮，路由随机种子依次为
7、17、27、37、47、57、67、77、87、97；各配置使用同一组种子，便于配对比较。
一次只改变一个 EWMA 参数，其余保持基准值：**滑动统计窗口 W=20 块**、
epsilon=0.95、半衰期 20 块、q_min=0.1、chi=10。冷启动预热要求
`min_blocks=5` 在全部候选中固定，不参与搜索。

三组搜索：

- EWMA 滑动统计窗口 W：5、10、15、…、50 块，共 10 个取值；
- deficit epsilon：0.05、0.10、0.15、…、0.95，共 19 个取值；
- EWMA 半衰期：1、2、3、…、50 块，共 50 个取值。

本实验的 EWMA 使用**有限滑窗指数加权**：只计入最近 W 块的需求，窗口内
按 `2^(-块龄/半衰期)` 加权，窗口外贡献严格为 0。W 控制硬截断范围，
半衰期控制范围内的衰减速度，预热块数始终固定为 5。这是专为本实验启用
的新统计模式；其他未设置 `tdr_ewma_window_blocks` 的实验保持原先无限历史
EWMA 语义。三个单因素搜索共有 79 个参数取值，
其中基准配置在三组内重复，去重后是 77 个 EWMA 候选。10 轮共计
770 次目标方案 × 10,000 CTX = **770 万笔 EWMA 测试交易**。执行器还会为
每轮运行并缓存复用 4 种对照臂，即额外 40 次 × 10,000 CTX；如果把所有
方案的链上运行都计入，总计约 **810 万笔 CTX**。这不是 77×10×5 次
独立对照运行；缓存避免了重复执行相同的对照配置。

运行：

```powershell
python experiments/exp019_clean_ewma_parameter_search/run.py
```

预览计划但不启动链：

```powershell
python experiments/exp019_clean_ewma_parameter_search/run.py --dry-run
```

输出位于 `out/<timestamp>/`，包含原始结果、聚合 CSV、参数影响表、推荐值和图。
这 10 轮都使用相同 clean 轨迹，只改变路由随机种子；箱线图反映固定轨迹下的
跨轮波动，不代表跨数据集泛化。旧单轮目录 `out/20260928_204029/`
扫描的是预热块数，而且旧 EWMA 没有有限滑窗；**不能把旧结果重命名为 W 扫描，
也不能直接 `--resume` 进新设计**。运行上述命令会创建新目录。
若新 10 轮实验中途停止，可用新目录执行：

```powershell
python experiments/exp019_clean_ewma_parameter_search/run.py --resume experiments/exp019_clean_ewma_parameter_search/out/<新目录名>
```

论文风格的三参数敏感性主图可仅使用已有结果重绘，不会重新运行交易：

```powershell
python experiments/exp019_clean_ewma_parameter_search/plot_sensitivity.py --run experiments/exp019_clean_ewma_parameter_search/out/<新目录名>
```

生成 `figs/exp019_paper_sensitivity.png` 和 `.pdf`。图中上排是 Relay 回退，
下排是完成的 TDR 搬运交易数；沿用 `brokerlab.plotting` 的论文配色。
同时生成 `figs/reference_style/` 下的三张独立双纵轴图（epsilon、EWMA 滑动窗口 W、
EWMA 半衰期，均提供 PNG 和 PDF），版式参照 `D:\实验\exp8_latest_plotting\reference_previews`。
当每个参数值的 10 轮结果全部完成且通过校验时，`reference_style/` 中绘制
真正的双 Y 轴分组箱线图，箱体基于 10 个逐轮原始值，非聚合后的中位数。
旧单轮目录不能用于新设计的 W 敏感性图；其旧图片可以保留作探索记录，
但不能混入新的三参数箱线图。
