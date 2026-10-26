# Exp046：五方法、每轮 100k、每方法三轮的统一对比

此实验重新执行所有五个方法。每次运行从同一份冻结代码、同一份用户 CTX 工作负载和全新的链开始，不复用其他批次的收益，也不按倍数推算较长实验。协议：`exp046-five-methods-100k-v1`。

## 正式参数

| 项目 | 设置 |
| --- | --- |
| 方法 | AutoLoop、Spider-rebalance、Proportional + EWMA、Top-up + EWMA (epsilon=0)、TDR (Top-up + EWMA) |
| 每方法轮数 | 3，路由种子 7、17、27 |
| 每方法每轮用户 CTXs | 100,000 |
| 单方法累计 CTXs | 300,000 |
| 总运行次数/总用户 CTXs | 15 / 1,500,000；不含内部再平衡转移 |
| Broker / shard / user | 50 / 16 / 200 |
| 每子账户初始余额 | 45 ETH；每 broker 16 个子账户，共 720 ETH |
| 用户流到达率 / 区块间隔 | 120 CTX/s / 1 s |
| 单 broker 工作负载配额 | 2,000 CTX，用于选取总量；实际动态路由分配可不同 |
| 工作负载 | ETH_cleaned.csv，唯一 CTX，金额范围 0.01–10 ETH；所有方法和轮次共用同一哈希 |
| 需求变化 | 从 arrival_pos=30,000 开始，每 4,000 笔在 shard 11、3 间切换目标；避免同分片 |
| Broker 服务收入 | 2,100,000 wei = 0.0021 Gwei / 笔 |
| 再平衡模型成本 | 21,000,000 wei = 0.021 Gwei / 完成的内部逻辑转移 |
| 利润 | 成功服务用户 CTXs × 0.0021 − 完成转移次数 × 0.021 Gwei |
| RPC 端口起点 / 运行锁端口 | 40600 / 45950；运行前只检查空闲，不终止其他实验 |

| 方法 | 分配/触发参数 |
| --- | --- |
| AutoLoop | low=0.30、high=0.70；容量 C=2×45=90 ETH/子账户；每 10 blocks 检查；目标带中点 45 ETH |
| Spider-rebalance | 成功 broker 服务的用户本金流量触发；阈值 0.25×16×45=180 ETH/broker；原生策略调度 |
| Proportional + EWMA | 比例分配；epsilon=0.95；q_min=0.30；EWMA half-life=20 blocks；检查间隔 chi=10 blocks |
| Top-up + EWMA (epsilon=0) | Top-up 分配；仅 epsilon 改为 0，其余同完整 TDR |
| TDR (Top-up + EWMA) | epsilon=0.95；q_min=0.30；EWMA half-life=20 blocks；chi=10 blocks |

三种 EWMA 方法均使用 20 blocks 的初始需求观察保护、20 blocks 的需求窗口和转移超时、单向 Top-up 设置（two_sided=false）。AutoLoop 与 Spider 使用各自策略，不把 TDR 的 chi 死区强加到它们身上。每轮轮换方法执行顺序，降低固定批次顺序影响。

所有方法均启用同样的 B2E 服务费：gas_units_per_ctx=21000、名义 ref_gas_price_wei=1000、fee_share=0.1；内部再平衡不再重复收取 B2E 费用。Anvil 实际 gas price 为 0。0.021 Gwei 是后处理经济模型成本，不是从实际余额扣除的链上手续费，不能称为实测 gas 成本。

## 运行

在项目根目录、已激活的 brokerlab 环境中：

```powershell
python experiments/exp046_unified_100k_comparison/run.py --dry-run
python experiments/exp046_unified_100k_comparison/run.py --background
```

默认命令就是正式 100k×3；不需要再传交易数或轮数，避免旧实验默认参数不一致的问题。后台进程隐藏窗口，不依赖当前终端保持打开。关闭电脑或终止 Python 仍会中断实验。

独立小规模联调，明确隔离于正式结果：

```powershell
python experiments/exp046_unified_100k_comparison/run.py --smoke --background --no-plot --base-port 40700 --lock-port 45951
```

Smoke 使用 2 brokers、2 shards、每方法 40 CTX、一轮，共五次运行；不能用于论文结论。联调不改变正式配置。

## 进度和恢复

`out/latest_run.json` 指向正式目录；`out/latest_smoke.json` 指向联调目录。每个运行目录含 `progress.md`、`progress.json`（每 15 秒更新）、`runner.log`、`manifest.json`。各任务有独立 `t/.../stdout.log`。

```powershell
python experiments/exp046_unified_100k_comparison/run.py --resume "完整运行目录" --background
```

恢复时核对冻结代码、公共工作负载、配置和已完成产物的 SHA256；完成的任务不重跑，失败或中断的任务创建新的 attempt，保留旧记录。不要对同一实验同时启动两个 runner。需要在当前任务结束后停止时，在对应运行目录创建一个空的 `STOP_AFTER_TASK` 文件；恢复前移走该文件。

## 结果与绘图

- 每轮每方法均保存原始 `ctx_rows.csv`、`tdr_moves.csv`、账本/链状态检查和 summary。
- 公共工作负载的 CTX ID、源/目标、金额和 arrival_pos 均逐条校验；成功完整、无重复丢失、余额守恒、burn/mint 对账通过后才标为 complete。
- `results.csv` 保存 15 组系统总量；`per_broker.csv` 保存 750 条真实 broker-run 记录。
- 通信量柱形使用每轮系统用户 CTX 通信账单的三轮均值，误差棒为 min–max，没有除以 broker 数。通信账单包含声明的 broker 控制字节（100 B/笔）和 relay 交易/回执字节，不包含未记录的再平衡通信和其他协议消息。
- 利润箱线图每方法 150 条观察值，显示 Q1–Q3、中位数、1.5 IQR 须和离群点；它们来自 3 轮，不是 150 次独立实验。
- 全部 15 次完成后自动生成 `figures/fig5e.pdf/png` 和 `figures/fig5e_panel.pdf/png`，图注和实际压缩的空白坐标区间保存在该目录。
- 绘图入口：`python "运行目录/code/plot.py" "运行目录"`。缺轮、失败任务和未标记的 smoke 数据不会进入正式图。

`runtime/` 是随实验附带的同一份冻结执行后端，基于先前已验证的 Fig5c 补充后端；AutoLoop 原生 band、Spider 成功流量统计和其余 EWMA 方法在这一份代码中执行。每次准备实验再次复制到结果目录并记录哈希，因此不会受 D 盘或其他实验后续改动影响。除 `trace/ETH_cleaned.csv` 外，无需访问其他批次结果。

## 验证

```powershell
python -m unittest discover -s experiments/exp046_unified_100k_comparison -p test_design.py -v
```

测试覆盖正式任务总量与成对种子、方法到引擎的明确映射、epsilon=0 消融的参数隔离、拒绝错误交易量/轮数/方法集合、统一名义收费和系统通信总量口径。
