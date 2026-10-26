# Exp045：Fig5b / Fig5c 独立 TDR OFF 补跑

仅执行 `plain`。原 TDR、AutoLoop、Spider 实验及其源码、manifest、工作负载、结果均不修改。

## 设计与利润

| 项目 | Fig5b | Fig5c |
|---|---|---|
| 扫描 | 系统用户CTX总数10k、20k、40k、60k、80k、100k、150k、200k | 每子账户初始余额15、30、45、75、100、150、200、300、500、1000 ETH |
| 固定条件 | 每子账户45 ETH | 全系统40k用户CTX |
| 轮次、种子 | 每档10轮；7、17、27、37、47、57、67、77、87、97 | 相同 |
| 新增运行 | 70 | 100 |
| 复用 | 实验41的45 ETH、40k、10轮，重验原始台账 | 不跨后端版本复用 |

合计180个观察值，其中170次新运行，10次旧结果复用。两个历史后端分开冻结，所有执行源码逐文件匹配对应原始manifest；Fig5c后端撤销旧补充实验的baseline补丁后验证为原始字节，不使用当前工作树的新版代码。只有空namespace初始化文件额外加入以隔离Python导入。

保留16分片、50 broker、200用户、120用户CTX/秒、发送窗口和其他执行参数；每个broker全部16个子账户都初始化为当前B。直接复制保存的 `w/N.csv` 并校验SHA256，不重新抽样、排序、分组或修改交易方向。只关闭内部再平衡；资金不足的用户CTX继续走relay。

固定手续费F=21,000,000 wei=0.021 Gwei。broker成功服务一笔用户CTX获得0.1F=2,100,000 wei=0.0021 Gwei。TDR OFF利润为 `served × 0.0021 Gwei`，内部再平衡事件、转移及成本必须均为0。relay用户CTX不给broker服务收入。每个观察值为Broker19全部16子账户汇总。

原With TDR的potential参考仍按其自身assigned用户CTX计算；动态匹配下其他方法的assigned数可能不同，因此不宣称此参考是所有方法共用的严格上界。

## 运行

在项目根目录PowerShell运行；默认新结果写到C盘 `C:/Users/hzy/Downloads/out/exp045_plain_sensitivity/时间戳`。

```powershell
python experiments/exp045_plain_sensitivity_supplement/test_plain.py
python experiments/exp045_plain_sensitivity_supplement/run.py --dry-run
python experiments/exp045_plain_sensitivity_supplement/run.py --smoke --background
```

小跑：两套原后端各重放2000笔，仍保留完整拓扑。标为smoke，禁止进入正式箱线图。小跑和正式任务使用同一独占锁，必须等小跑结束后启动正式任务。

```powershell
python experiments/exp045_plain_sensitivity_supplement/run.py --background
```

新任务只有一个运行器，内部串行交替推进两种扫描，最多新增一套16分片链，使用低于正常的进程优先级。独占锁45948、RPC从38600寻找空闲连续段，与原45940/45946和28600/36600隔离。可与原实验并行，但共享CPU/内存/磁盘，耗时可能变化。仅复制和读取D盘旧数据，后续正式运行从私有快照执行。

`--only b` / `--only c` 可以单独创建扫描计划；默认both。`--no-reuse`会将Fig5b原先复用的10次也重新执行。可用 `--output-root "D:/exp045_results"` 指定新输出位置；`--fig5b-base`、`--fig5c-base`、`--reuse-plain`可替换输入路径，但必须通过历史哈希和配对校验。不要重复创建相同正式计划；已启动的计划使用续跑。

```powershell
python "完整结果目录/code/run.py" --resume "完整结果目录" --background
```

运行器重新检查冻结代码、输入、已完成证据后跳过已完成任务；失败任务保留attempt并创建新attempt。`--max-tasks 2`可验证完成两次后停止及后续续跑。创建结果目录下 `STOP_AFTER_TASK` 空文件后，当前任务校验完成即安全停止；续跑前移除该文件。不得修改结果目录backend/code里的文件。

## 状态与证据

结果目录 `运行状态.md` 每30秒更新。`manifest.json`记录每个任务状态、来源、种子和独立attempt；`report.json`分开记录新增完成与复用完成。每个attempt包含原始ctx_rows、tdr_moves、链日志、stats、有效配置、全broker记账和证据SHA256。复用10次也复制原始CSV与summary，独立重算且逐broker比较；不会把“复用”统计成新执行。

所有守恒、用户CTX完整成功、无未结算转移、利润一致性校验通过后才标为complete。失败即停止，不将失败当作亏损数据。工作负载、旧结果、受检执行代码的哈希保护不会因为其他实验新增文件而失效。

## 合并绘图

当本补跑与原AutoLoop/Spider均完成后：

```powershell
python "完整结果目录/code/plot.py" "完整结果目录"
```

生成 `figs/fig5b_with_plain.pdf`、`fig5c_with_plain.pdf` 及600dpi PNG。页面125.6×134.7 pt；绘图区左25.5、上16.5、宽93.7、高94 pt，与原图对齐；图例右下角。五组箱线图：Broker with TDR、Broker without TDR、AutoLoop、Spider-rebalance、Potential profit of Broker19。Q1–Q3、median、1.5IQR须线，不制造方差或补齐未完成轮次。

`--allow-partial --png-only`只预览四种方法全部完成的配对单元，显式显示PARTIAL；不得当作正式全轮结果。`--only b` / `--only c`可选图。
