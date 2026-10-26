# exp033 修复版运行指南：exp033-faithful-v2

算法思想、原文页码、逐个例子与复现边界见 [ALGORITHMS_CN.md](ALGORITHMS_CN.md)；逐项修复与验证见 [FIXES_CN.md](../../FIXES_CN.md)。这版不预设哪种算法获胜。

## 本机 PowerShell 命令

```powershell
Set-Location 'C:/Users/hzy/Downloads/exp_anvil_broker_qwen/exp_anvil_broker_qwen/output/exp033_main_audit'
# 计划：11 臂、3 场、40,000 CTX/臂，共 33 个串行任务
python experiments/exp033_sota_comparison/run.py --dry-run
# 真实选样、训练隔离与 PnP 可行性；不启动链
python experiments/exp033_sota_comparison/preflight.py
# 链路检查：1 broker、80 CTX/臂、1 场、45 ETH/片、PnP epoch=1 块，formal=false
python experiments/exp033_sota_comparison/run.py --smoke
# 分析档：40,000 CTX/臂、1 场，formal=false
python experiments/exp033_sota_comparison/run.py --rounds 1
# 正式对比：11 臂×3 场×40,000 CTX=1,320,000 个评估 CTX
python experiments/exp033_sota_comparison/run.py
```

anvil.exe 已在本机 PATH。已建立 Git 忽略的 trace 目录 junction，指向原仓库数据，没有修改或复制原始 CSV。迁移代码后需提供 trace/ETH_cleaned.csv，或每次加 `--set 'traffic.real_csv_path=<绝对路径>'`。

run 目录为 `experiments/exp033_sota_comparison/out/<时间戳>`，终端会打印完整路径。恢复时必须保留首次运行的全部参数：

```powershell
python experiments/exp033_sota_comparison/run.py --resume '<正式 run 目录>'
python experiments/exp033_sota_comparison/run.py --rounds 1 --resume '<分析 run 目录>'
python experiments/exp033_sota_comparison/plot_pareto.py --experiment-source '<run 目录>' --output '<run 目录>/figs'
```

恢复期间源码、参数、数据发生变化会拒绝继续。源码运行中变动也会检测。旧代码结果不与新版合并。

分批跑可用 `--set 'exp.arms=plain@45,loopmax@45@@0.0@@20@0.0'`。完整出图需各臂覆盖同样场次、同 seed/规模的 plain；合并时还检查源码、训练/评估 hash、公共配置、同名臂 token 和守恒门。重复的同臂同场次拒绝合并。子集不足 11 臂仍显示分析标记。

## 参数、产物和范围

新参数均在 config.yaml：训练 12,000 CTX，PnP epoch=10 块、outage=.05；参考容量=2×初始单片余额；检查/预测确认各 10 块，Loopmax horizon=20、安全额=2 块；Spider 成功金额间隔=.25×初始资本。这些容量、时间与金额是平台标定，不能声称原文绝对单位默认值。

参数稳健性用独立 run，例如：

```powershell
python experiments/exp033_sota_comparison/run.py --rounds 1 --set 'exp.sota_spider_vol_mult=0.5'
```

关键产物：manifest.json 保存版本、源码指纹、训练/评估 hash 和种子；params.json 保存公共配置与训练窗口；training.json/workload.csv 分别保存训练和评估数据；report.json/raw_results.csv 保存每臂每场指标。session 目录内有逐臂 summary、ctx_rows.csv、tdr_moves.csv、coordinator.json，PnP 有 initialization.json；出图生成 figs/exp033_pareto.png 和 SUMMARY_CN.md。

补资等待保存为 repair_wait_secs 并加入 e2e_secs。PnP 在评估前充值，初始规划不计在线搬运，其最低资本与固定预算扩展另存。全部在线搬运仍是 burn/mint，需要通过守恒门。

PnP-static 是单跳、零费用、单次 epoch 初始化特例；Autoloop/Loopmax 是封闭资金池适配；Spider 仅取再平衡模块。Flexichain/UCRB 在当前平台资金规则等价。详细边界与例子见算法手册，运行成功不表示完整 PCN 或密码协议复现成功。

修复保存在 fix/exp033-faithful-baselines 分支，本次未推送 GitHub。源码 zip 便于运行，Git bundle 保留提交历史。trace 与实验产物不进入 Git。实际性能结论等待完整实验数据。

## D盘本地同步修复（2026-10-11）

当前目录已同步 exp033-faithful-v2，相关105项测试通过。运行 `python experiments/exp033_sota_comparison/run.py` 即为11臂×3场×40,000 CTX。运行结束自动生成老师建议的三张二维散点图：relay数 vs 再平衡CTX数、搬运总量ETH、再平衡CTX占比；PDF宽177.94mm、10pt标签。原始数据和旧out目录未修改，但旧版run不能继续resume，需启动新run。原文件备份在 `_codex_backups/exp033_before_20261011_211830`，包含修改清单与文件哈希。
