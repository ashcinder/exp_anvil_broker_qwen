# Fig5c：在已完成 Exp040 上补跑 AutoLoop 与 Spider-rebalance

此目录是 Exp040 的独立补充入口，不修改旧 `run.py`，不重跑 TDR / Plain，不重新抽取交易，也不覆盖旧实验结果。整个目录包含冻结运行代码，复制到 C 盘或 D 盘的项目中均可运行，不需要再次下载后端。

## 固定实验参数

| 项目 | 设置 |
| --- | --- |
| 新方法 | `autoloop`、`spider`，不允许调度其他方法 |
| 余额 B | 15、30、45、75、100、150、200、300、500、1000 ETH |
| 余额单位 | 每个分片子账户；所有 50 个 broker 均同时设置为 B；单 broker 总初始资金为 16B |
| 重复 | 每档 10 轮，每方法 100 次，共新增 200 次运行 |
| 随机种子 | 工作负载沿用原文件；路由种子 7、17、27、37、47、57、67、77、87、97 |
| 交易量 | 每轮全系统 40,000 笔，50 个 broker 共同服务，并非每个 broker 各 40,000 笔 |
| 拓扑 | 16 分片，50 broker，200 用户，1 秒出块 |
| 输入 | 原结果目录 `w/40000.csv`，SHA256 必须匹配 manifest，不重新抽样或分组 |
| 速率 | 120 笔用户 CTX/秒 |
| 方向变化 | 原 CSV 已包含：从索引 12,000 起，每 1,600 笔在目标分片 11 / 3 之间切换；不再二次改写 |
| 固定手续费 F | 21,000 × 1,000 = 21,000,000 wei = 0.021 Gwei |
| 服务收入 | Broker 路径成功完成一笔用户 CTX，获得 0.1F = 0.0021 Gwei；relay 路径收入为 0 |
| 再平衡成本 | 每次完成的源 burn / 目标 mint 转移计一次，成本 F = 0.021 Gwei；不把两腿重复计为两次转移 |
| 利润 | `served_user_CTX × 0.0021 - completed_rebalance_transfers × 0.021`，Gwei |
| 绘图对象 | ID 为 19 的 Broker，汇总其全部 16 个分片子账户 |
| 实际链上 gas price | 0；上述成本为与旧实验相同的名义手续费模型，不是实测 gas 支出 |

AutoLoop 沿用 Fig5a/Exp043 的决策规则：参考容量 C=2B，低/高阈值为 0.3C / 0.7C，仅对越界账户提出调回 0.5C 的请求，每 10 块检查。闭合资金池内通过实际 burn/mint 转移配对执行；不是外部支付通道或 swap 服务的完整复现。

Spider-rebalance 沿用同图的适配规则：累计**成功用户交易本金**达到 `0.25 × 16B = 4B ETH` 后，均衡子账户可用余额。不用已提交需求、不把 relay、手续费或内部再平衡金额计入成功流水。两种 baseline 都不套用 TDR 的 chi 等待和启动错峰；事件仍互斥，沿用相同转移确认/超时实现。

共同设置继承原任务 `summary.json` 中的 `effective_config`，而非当前安装的 YAML：包括发送窗口、RPC 刷新、并发上限、7200 秒 drain 尾部、B2E 费用机制等。TDR 特有的 epsilon、q_min、EWMA 不作为这两个 baseline 的调优参数。

## 直接运行

在项目根目录的 PowerShell 中执行。把 `--base-run` 改成**已完成 100 次余额扫描**的结果目录（内含 manifest/config/report/per_broker、t、w），不要传只含 figures 的目录。

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/run.py --base-run "C:/Users/hzy/Downloads/out/out/20261016_003612_099146" --dry-run
```

小规模链上验证：保留原 50 broker、16 分片、200 用户，原交易文件的前 2,000 笔、两个新方法；不改写 sender/shard 映射，有明确 `smoke` 标志，不混入正式图：

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/run.py --base-run "C:/Users/hzy/Downloads/out/out/20261016_003612_099146" --smoke
```

正式补跑全部 200 次，并在完成后生成新的 Fig5c：

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/run.py --base-run "C:/Users/hzy/Downloads/out/out/20261016_003612_099146"
```

加 `--background` 可隐藏窗口后台执行，路径和 PID 会打印出来，日志在结果目录 `runner.log`。请勿重复启动同一计划。

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/run.py --base-run "C:/Users/hzy/Downloads/out/out/20261016_003612_099146" --background
```

如要把结果存 D 盘，加 `--output-root "D:/exp040_sota_results"`；结果目录与原结果必须分开。若 anvil 不在 PATH，加 `--anvil-bin "C:/Users/hzy/.foundry/bin/anvil.exe"`。RPC 从 36600 寻找连续空闲端口；独占锁为 45946，与原实验分开，不结束其他程序。

## 续跑和选择子集

续跑只跳过已经通过原始数据校验的任务；失败或中断任务保留原 attempt，另开新 attempt：

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/run.py --resume "完整的新增结果目录"
```

可选：`--methods autoloop` 或 `--methods spider` 只跑一个方法；`--balances 15,45,1000` 选择现有余额；`--rounds 2` 仅选择原来的前两轮，种子仍为 7、17。默认全选。分别运行单方法会形成两个独立计划；四组箱线图的自动合并入口要求同一个计划包含两个方法，因此建议默认一起运行，程序会串行交替执行它们。

`--max-tasks 2`：只完成当前计划中的两个新增任务后退出，之后用 `--resume` 继续原计划，仍保留完整 200 次计划。

安全地在当前任务完成后停止：在新增结果目录创建名为 `STOP_AFTER_TASK` 的空文件。续跑前移除此文件。程序不会因为其他实验代码改动而报“Source changed”：每次新计划都复制私有代码快照到 `backend/` 和 `code/`，续跑校验的是这些快照。

## 输出与绘图

每个任务保留 `params.yaml`、链日志、`ctx_rows.csv`、`tdr_moves.csv`、统计 gates、每 broker 记账、实际生效配置和证据文件哈希。原 Exp040 的守恒、无重复无丢失、全部成功、无未结算转移等检查必须通过才能标记完成，失败即停止，不把失败算作低收益。

新增结果目录中的 `reference/` 为旧结果的只读副本，`w/40000.csv` 为原工作负载副本。`results.csv`、`per_broker.csv` **只保存新增方法**，原 TDR 100 次不会重复执行。

完成后 `figs/fig5c_broker19.pdf` 为矢量图，另有 600dpi PNG，尺寸 125.6 × 134.7 pt，绘图区 `[25.5,16.5,93.7,94]` pt，与当前 Fig5c 相同。每档四组箱线图：原 TDR 利润、AutoLoop 利润、Spider 利润、原 TDR 分配交易对应的潜在利润。纵轴为 `Profit of Broker 19 (Gwei)`，图例放右下角；使用线性纵轴自动显示全部收益和亏损范围。

每个箱子是该余额下跨轮分布，Q1-Q3、median、1.5IQR 须线，不添加人造波动。动态匹配下各方案实际分给 Broker19 的交易数可不同；蓝色潜在利润保留旧 TDR 的 assigned 口径，**不能宣称它是所有方法共用的严格上界**。每个新方法自己的 assigned、upper、served、relay、cost 都另行保存。

缺任务时不自动生成正式图。可显式预览只含完整配对数据的局部结果（会标 `PARTIAL`），不补零或补齐缺失轮次：

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/plot.py "新增结果目录" --allow-partial
```

## 版本核查

`runtime/provenance.json` 记录从历史提交 `3e1dd1e2c0de6cc49f05c7fc6c57d23156fa91e6` 恢复的 26 个文件的哈希，逐一匹配这批旧实验的 manifest。公共 broker engine、路由协调器函数、资金初始化、TxService、账本和手续费记账保持历史版本。只在私有运行副本中接入两个对比策略：AutoLoop 的 native band 请求/容量/检查周期、Spider 的成功流水回报、baseline 独立 chi/offset；每处差异可查 `freeze_backend.py`。

测试：

```powershell
python experiments/exp040_liquidity_scale/sota_supplement/test_supplement.py
```

测试含真实旧任务收益重算、转移台账缺失拒绝、源文件篡改检测、配对种子/选择器、禁止重跑 TDR、AutoLoop 阈值及检查周期、Spider 成功流水语义、部分绘图不补零。
