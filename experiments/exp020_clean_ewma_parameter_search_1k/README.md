# 实验20：Clean 数据集上的 EWMA 参数敏感性（1,000 CTX × 10 轮）

实验20沿用实验19当前的有效搜索设计和有限滑窗 EWMA：窗口内按
`2^(-块龄/半衰期)` 加权，窗口外贡献为零，预热块数固定为5。
数据为 `trace/ETH_cleaned.csv`；固定 50 brokers、16 分片、每 broker 初始余额
150 ETH、目标注入速率120 CTX/逻辑块，交易金额过滤范围0.01～10 ETH。

每个参数候选运行10轮，种子为7、17、27、37、47、57、67、77、87、97；
每轮每个方案1,000 CTX（50 brokers × 20 CTX）。每次只改变一个参数：

- EWMA 滑动窗口 W：5、10、15、…、50，共10个值；
- deficit epsilon：0.05、0.10、…、0.95，共19个值；
- EWMA 半衰期：1、2、3、…、50，共50个值。

epsilon=1不纳入搜索，因为策略要求 epsilon < 1。基准设置 W=20、epsilon=0.95、
半衰期=20在三组中共享，去重后是77个唯一候选。目标 EWMA 工作量为
77 × 10 × 1,000 = **770,000 CTX**。执行器还会为10个路由种子运行并缓存
4种对照方案，共增加40 × 1,000 CTX；计入对照方案后总量约 **810,000 CTX**。

## 运行

在仓库根目录 PowerShell 先预览计划（不会启动链）：

```powershell
python .\experiments\exp020_clean_ewma_parameter_search_1k\run.py --dry-run
```

确认计划显示77个唯一候选、10轮、每 arm 1,000 CTX 后，启动正式实验：

```powershell
python .\experiments\exp020_clean_ewma_parameter_search_1k\run.py
```

输出写入 `experiments/exp020_clean_ewma_parameter_search_1k/out/<时间戳>/`。
启动器会从配置中的首选端口10600开始，检查并自动选择一段连续可用的16个
localhost端口，然后将所选端口写入运行清单。它不会连接或结束占用端口的进程。
实验19仍在运行时，实验20会选择另一段空闲端口；两组实验同时运行会显著增加
CPU、内存和磁盘负载，建议错开执行。

中断后用实际创建的目录续跑。若清单记录的端口仍被占用，启动器会重新分配并
更新清单后续跑：

```powershell
python .\experiments\exp020_clean_ewma_parameter_search_1k\run.py --resume .\experiments\exp020_clean_ewma_parameter_search_1k\out\<时间戳>
```

此前因10600–10615被占用而中断的运行目录可用同一命令续跑：
`out\20260929_125009`。失败场次会重试，之前成功且完整的场次会跳过。

全部候选完成后生成论文风格敏感性图（双Y轴分组箱线图）：

```powershell
python .\experiments\exp020_clean_ewma_parameter_search_1k\plot_sensitivity.py --run .\experiments\exp020_clean_ewma_parameter_search_1k\out\<时间戳>
```

箱线图使用每个参数值10轮的逐轮结果；只有某一轴的全部候选均通过完整性与
守恒校验时才绘制箱体。各轮使用同一 clean 轨迹，仅路由种子不同，因此箱线图
描述的是固定工作负载下的跨路由轮次波动。
