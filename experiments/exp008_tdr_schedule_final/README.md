# exp008 · Clean 主网 trace 正式验证

## 当前配置：恢复最初的资金与交易金额参数

实验 8 当前使用清洗数据 `trace/ETH_cleaned.csv`，并排除合约端点。
初始余额（含五个 arm 的 fund）恢复为 150 ETH，交易金额过滤恢复为 0.01～10 ETH。
按用户要求仅运行 1 个 session；单场结果不代表统计显著性。
历史修复代码保留，未运行实验。下方Valve扫描及余额搜索章节仅作历史记录。

| 参数 | 当前值 |
|---|---|
| 数据集 | ETH_cleaned.csv；排除合约端点 |
| 初始余额 | 每 broker 每分片 150 ETH；50 brokers × 16 分片，总计 120000 ETH |
| 金额过滤 | 0.01～10 ETH；过滤记录，不裁剪金额 |
| 方案 | Plain、Valve=1.3、Proportional ε=0.1、硬窗口Topup ε=0.1、EWMA Topup ε=0.95 |
| 硬窗口／EWMA半衰期 | 20块／20块 |
| q_min／chi／timeout | 0.1／10块／20块 |
| offset／poll | 3块／1秒；spread沿用当前代码默认5 |
| 规模 | 1 个 session，每方案 80000 笔；五方案共 400000 笔 CTX |
| 注入目标 | 120 CTX/逻辑块；不是强制 120 CTX/墙钟秒 |
| 并发 | sender_window=2，max_inflight=24（恢复原始值） |

Valve实际阈值由arm中的1.3决定；全局tdr_cap_mult=2.0只是原始备用值。
已移除Valve扫描开关/阈值列表/每方案10000笔等新增控制项，run.py默认不启用扫描。
session 数量在 `exp.exp008_sessions` 调整；每方案总笔数请直接修改 `exp.exp008_ctx_total`。
程序会自动换算内部的 `ctx_per_broker`，并在终端打印总数和换算公式。
十六个 Anvil 分片以最慢块高作为全局逻辑时钟；Windows 调度下逻辑块可能慢于理想的 1 秒，
因此报告的墙钟注入 CTX/s 可能低于 120，但不代表每逻辑块的到达目标失效。
小数余额解析和结果记录精度等bug修复未回退，策略代码未改。

最大单笔 10 ETH 低于每个 broker 每分片 150 ETH 的初始余额，不会因单笔金额大于初始余额而先天无法支付。
并发已恢复原值，不能保证实际吞吐仍达到 110～120；本轮不预设 EWMA 获胜。

```powershell
cd D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen
python experiments/exp008_tdr_schedule_final/run.py
```

只检查生效参数、不启动实验：在上述命令末尾加 `--dry-run`。
不要调用旧的calibrate_balance.py，它仍是历史余额搜索入口，会使用不同参数。
本次修改前文件备份在 `backups/20260927_191339_restore_original/`；恢复时将config.yaml和
README.md复制回本目录，将test_exp008_valve_grid.py复制回项目tests目录。历史结果不覆盖。

---

以下均为历史配置，不代表当前默认。

## 历史入口：固定余额Valve阈值扫描（2026-09-27 15:49）

本节为当时的运行说明，目前默认已关闭扫描。
**使用 `run.py`，不要用旧的 `calibrate_balance.py`，后者是之前的余额搜索。**

```powershell
cd D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen
python experiments/exp008_tdr_schedule_final/run.py
```

已经进入本实验目录时，直接 `python run.py`。预览参数可加 `--dry-run`，不会创建实验或启动链。

编辑 `config.yaml` 的下列字段即可调整，无须修改Python代码：

```yaml
exp:
  exp008_valve_sweep: true
  exp008_valve_thresholds: [1.05, 1.1, 1.3, 1.5, 2.0, 2.5]
  exp008_ctx_per_method: 10000
  exp008_sessions: 1
  balances_eth: 0.5
```

默认六个Valve方案，加Plain、Proportional、硬窗口Topup、EWMA四个共享对照，
一共10组 × 10000笔 × 1 session = **100000笔CTX**。对照不随每个Valve阈值重复跑。
所有方案固定每broker每分片0.5 ETH、同一trace、同场路由种子；每组仍重新起链。
保留金额过滤0.001～1 ETH、ETH_cleaned.csv、120 CTX/秒目标、50 brokers、16分片；排除合约端点。
EWMA epsilon=0.95/半衰期20块，Proportional和硬窗口Topup epsilon=0.1/窗口20块不变。
六个Valve的绝对触发水位分别为0.525、0.55、0.65、0.75、1、1.25 ETH。

开启扫描时，`balances_eth`、阈值列表、每方案笔数是权威输入；程序自动生成 `arms`、
`ctx_per_broker`、`broker.initial_balance_eth` 和 `exp008_final_arm` 并传给子进程。
每方案笔数必须能被broker数量整除。不要只编辑派生字段来改变扫描。
原配置文件不会被运行入口改写，实际生效配置保存在各轮summary及最终report中。
`exp008_valve_sweep: false` 恢复旧的手动arms模式，方便兼容旧实验配置。

输出仍为 `out/<时间戳>/report.json` 及 `session_1/<时间戳>/summary.json`，
包含各阈值的Relay、TDR事件、转移数、吞吐和校验结果。只有一个session时是单次值，
不代表统计显著性。入口不会接着进行余额搜索或自动追加第二轮。

修改前的run.py/config.yaml/calibrate_balance.py/README.md备份在
`backups/20260927_154922_valve_grid/`，按同名复制回本目录即可恢复；新增测试为
`tests/test_exp008_valve_grid.py`。所有历史结果保持不变。

---

以下为历史余额搜索说明，不是当前默认运行方式。

## 当前配置与运行方式（2026-09-27 第二轮：极低余额＋Valve高阈值压力对照）

本节及 `config.yaml` 优先于下方历史记录。**本次仅修改代码，没有启动实验。**
余额 0.2 ETH 是上一轮选中值，仅作为新筛选前占位，不是本轮已测最优；不直接运行旧入口来代替余额筛选。

**本轮Valve刻意采用不利于及时触发的高阈值10.0，属于参数失配压力对照。**
它不是经实测确定的最差阈值，也不能用本轮结果宣称EWMA优于充分调优的Valve。
原Valve=1.3的完整结果保留在 `out/20260927_095443/`，不可改名为本轮结果。

在项目根目录运行（已激活 brokerlab 环境且 Anvil 可用）：

```powershell
cd D:\BaiduNetdiskDownload\exp_anvil_broker_qwen\exp_anvil_broker_qwen
python experiments/exp008_tdr_schedule_final/calibrate_balance.py
```

如果已经位于 `experiments/exp008_tdr_schedule_final`，使用 `python calibrate_balance.py`。
默认一次完成 8 档余额筛选 → 保存选定配置 → 运行正式两轮。
`--dry-run` 只打印计划，不启动链、不写文件；`--pilot-only` 只筛选和更新配置，不跑正式轮。
不要同时运行其他使用 8600–8615 端口的实验；入口发现端口占用会停止，不杀其他进程。

| 参数 | 当前设置 |
|---|---|
| 数据集 | `trace/ETH_cleaned.csv`；排除合约端点 |
| 单笔金额过滤 | 0.001–1 ETH；只过滤，不裁剪/缩放金额 |
| 初始余额搜索 | 每 broker 每分片 0.005、0.01、0.05、0.1、0.2、0.3、0.4、0.5 ETH |
| 系统规模 | 50 brokers × 16 分片；全体初始流动性为所选余额 × 800 |
| 筛选规模 | 8 余额 × 5 方法 × 5,000 CTX = 200,000 CTX；路由种子107 |
| 正式规模 | 选定余额 × 5 方法 × 2 轮 × 40,000 CTX = 400,000 CTX；种子7/17 |
| 注入与并发 | 120 CTX/逻辑块，块时间1秒；sender_window=8，max_inflight=64 |
| 硬窗口 | 20 块 |
| EWMA 半衰期 | 20 块（不是只保留最近20块） |
| TDR 间隔/超时 | chi=10块；timeout=20块；offset=3，spread=5；轮询1秒 |
| TDR 目标参数 | q_min=0.1；target_cap=0；min_reserve_eth=0 |

| 方法 | epsilon | 需求估计 | 其他阈值 |
|---|---:|---|---|
| Plain | — | 无 | 不再平衡 |
| Valve（高阈值压力对照） | — | 无 | 水位达到初始余额 × 10.0 才触发 |
| Proportional | 0.1 | 硬窗口20块 | excess_only触发后按目标重排 |
| Topup硬窗口 | 0.1 | 硬窗口20块 | 缺钱侧 < 0.9τ；供钱侧 > 1.1τ |
| Topup EWMA | 0.95 | 半衰期20块 | 缺钱侧 < 0.05τ；供钱侧 > 1.95τ |

EWMA与硬窗口策略参数移植自 exp011；保留 exp008 的速率与并发设置，仅扫描余额；Valve阈值固定10.0。
金额上下界本轮固定，不做联合最优化。EWMA 的深死区在低余额场景可能仍然不利，
这里是在检验迁移效果，不预先认定它更好。硬窗口与EWMA的epsilon不同，不能解释为纯估计器消融。

Valve绝对触发水位如下（不是要补到的目标余额）：

| 初始余额B（ETH） | 0.005 | 0.01 | 0.05 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Valve触发水位10B（ETH） | 0.05 | 0.1 | 0.5 | 1 | 2 | 3 | 4 | 5 |

Valve触发后，仍按原算法用最满分片超出B的资金，向低于B的分片补钱，未改策略实现。
10倍初始余额意味着须明显集中资金才触发，可能推迟补货；但它也可能因少搬运而降低TDR次数，
所以不保证Relay或Relay+转移数最差。0.005档全体Broker初始资金仅4 ETH，
而单笔过滤上限仍1 ETH：过低余额可能使所有方法都频繁Relay，不一定更有利于EWMA。

### 选择规则与结果

先要求 Plain Relay 占比至少2%，且EWMA确实产生TDR事件，排除没有实验压力的候选。
在合格候选中，优先最大化 `(其他四方法最低Relay − EWMA Relay) / 5000`；
同分时选择 EWMA 的 `Relay + 成功TDR转移数` 更少者，再同分取余额更低者。
若所有差值非正，仍会报告并验证选中的相对较好候选，**不会称其为EWMA获胜**。
若全部候选压力不足、结果缺失、校验失败或出现丢失转移，停止，不自动跑正式轮。

产物位于 `calibration/<时间戳>/`：

- `manifest.json`：预先记录的规模、选择规则和参数；`source_hashes.json`：源代码哈希。
- `balance_*/run.log` 和 `balance_*/out/*/summary.json`：每档完整日志及五方案结果。
- `pilot_metrics.csv`：40行方法级指标，随每档完成追加汇总；保留全部好/坏结果。
- `balance_ranking.csv`、`selection.json`：余额排名、是否合格、EWMA是否真的领先。
- `formal.selected.yaml`：正式配置；同时更新本目录 `config.yaml`，运行中用户修改过则不覆盖。
- `formal.log`、`status.json`：正式过程、最终报告路径及状态。

TDR事件数、成功资金转移数、链上交易段数不是同一单位：一次事件可以有多次转移，
成功转移通常有burn/mint两段。CSV中的 `relay_plus_transfers` 不是总链上交易数量；
同时提供 `tdr_events`、`tdr_transfers`、`tdr_chain_legs` 和实际吞吐，不混用口径。
正式报告仍在 `out/<时间戳>/report.json`；完整但假设不成立与运行失败分别记录。

5000笔约42秒的纯注入时长，20块半衰期仅覆盖约2个半衰期，因此筛选仍有冷启动误差。
正式40000笔用于更长时间验证。筛选是正式trace的前缀，换路由种子不等于独立时间窗口验证；
所有“选中”结论仅限这些候选和负载，不能称为全局最优或普遍泛化。

### 本轮修改及恢复（20260927_131153）

更改余额网格、Valve固定倍数及所有生成配置和标签；在manifest、CSV、selection和正式配置中注明压力对照。
`exp003/run.py` 的结果字段 `fund_eth` 不再四舍五入到两位小数，以免0.005被记录为0.01；
不改变实际注资或交易执行逻辑。读取筛选结果时也会核对余额原值。
新增小数余额、阈值触发逻辑及自动流程回归测试。

本轮修改前六个文件原样保存在 `backups/20260927_131153_valve_stress/`。
停止实验后，在项目根目录执行以下命令可恢复至上一轮Valve=1.3及旧余额网格：

```powershell
$backupDir = 'experiments/exp008_tdr_schedule_final/backups/20260927_131153_valve_stress'
Copy-Item -LiteralPath "$backupDir/config.yaml" -Destination 'experiments/exp008_tdr_schedule_final/config.yaml'
Copy-Item -LiteralPath "$backupDir/calibrate_balance.py" -Destination 'experiments/exp008_tdr_schedule_final/calibrate_balance.py'
Copy-Item -LiteralPath "$backupDir/README.md" -Destination 'experiments/exp008_tdr_schedule_final/README.md'
Copy-Item -LiteralPath "$backupDir/exp003_run.py" -Destination 'experiments/exp003_tdr_on_off/run.py'
Copy-Item -LiteralPath "$backupDir/test_exp008_calibration.py" -Destination 'tests/test_exp008_calibration.py'
Copy-Item -LiteralPath "$backupDir/test_config.py" -Destination 'tests/test_config.py'
```

旧输出不覆盖、不删除。若之后继续修改过上述文件，恢复前先保留那些新修改。

### 上一轮修改及恢复（历史）

修复 `brokerlab/config.py` 中 `initial_balance_eth` 的整数类型：改为float，避免小数余额
在配置快照中截断或CLI小数覆盖失败。实际实验注资另从arm token读取，不能据此判定旧链上注资错误。
新增 `calibrate_balance.py` 和无链单元测试 `tests/test_exp008_calibration.py`；旧runner未改。

修改前四个文件原样保存在 `backups/20260927_083734_low_balance/`。
停止实验后，在项目根目录执行以下命令可恢复这四个文件（若以后又改过文件，请先保留后续修改）：

```powershell
$backupDir = 'experiments/exp008_tdr_schedule_final/backups/20260927_083734_low_balance'
Copy-Item -LiteralPath "$backupDir/config.yaml" -Destination 'experiments/exp008_tdr_schedule_final/config.yaml'
Copy-Item -LiteralPath "$backupDir/README.md" -Destination 'experiments/exp008_tdr_schedule_final/README.md'
Copy-Item -LiteralPath "$backupDir/brokerlab_config.py" -Destination 'brokerlab/config.py'
Copy-Item -LiteralPath "$backupDir/test_config.py" -Destination 'tests/test_config.py'
```

新增脚本/测试及新输出是独立文件，恢复旧配置后不调用新入口即可；如需彻底移除，
只删除本次新增的 `calibrate_balance.py` 与 `tests/test_exp008_calibration.py`。
筛选脚本每次也会保存 `config.before.yaml`，用于单独撤销当次自动选参。旧结果不会覆盖或删除。

---

## 历史配置记录（以下不代表当前默认）

## 旧运行配置（2026-09-26）

本轮按Relay计数选择参数，不是公平的各方法最优排名，也不是已证明的全局最优。
默认两轮，路由种子7/17；每轮六组（五种方法及一个Valve选参敏感性组），
每组40,000笔，总计480,000笔CTX（不含TDR链上交易）。原Valve=1.3主对照保留。
注入120 CTX/s，16分片、50 brokers；每broker每分片初始1.5 ETH，
每broker合计24 ETH、全体合计1,200 ETH。sender_window=8，max_inflight=64。
继续使用 ethereum_202509.csv、允许合约端点，金额范围0.25～3 ETH为过滤条件。

| 方案 | ε | 估计器 | q_min | 其他 |
|---|---:|---|---:|---|
| Plain | — | — | — | 不做TDR |
| Valve | — | — | — | 阈值倍数1.3，保留原对照 |
| Proportional | 0.10 | 硬窗口6块 | 0.10 | 全量重排 |
| Topup硬窗口 | 0.65 | 硬窗口6块 | 0.30 | 只补缺口 |
| Topup EWMA | 0.50 | 半衰期12块 | 0.60 | 最新两轮已测Relay最少，综合开销非最低 |
| Valve选参敏感性 | — | — | — | 倍数1.5；不能替代1.3主对照 |

公共配置：χ=2块、供给侧ε=0.10、目标上限20%、绝对库存参数0.25 ETH，
错峰1～4块、超时20块、出块1秒。q_min会被绝对库存换算值抬高，
它不是固定余额保证。EWMA缺货触发为余额低于目标50%，硬窗口仍为35%；
供货均为高于目标110%。本轮只调EWMA，故不再是单独隔离估计器的消融。

### 调参依据与解释限制

最新完整实验out/20260926_122641的两轮结果：EWMA Relay为7350/7266，
Valve=1.3为7482/7758。EWMA Relay中位7308，Valve为7620；
但EWMA搬运中位7560、R+T中位14868，Valve为2362.5和9982.5。
因此保留已测Relay领先的EWMA ε=0.5、hl=12、q_min=0.6，不声称其综合开销最优。
更长半衰期可能稳定目标但也增加滞后，提高库存地板会减少需求偏置，降低ε可能增加搬运。
不能从联合参数变化中归因单个参数，也不能保证新一轮继续领先。
hl=12时每块衰减约0.9439，指数核总权重约17.82块；不是17.82块硬截断。
q_min=0.60在可用资金池24 ETH时对应每分片目标地板约0.90 ETH，
实际资金池及目标约束会影响最终目标，不代表账户永远保持0.90 ETH。

额外Valve改为1.5：最满分片达到2.25 ETH才触发，主对照1.3为1.95 ETH。
选择依据是exp010/out/20260926_092653的1000笔、1 ETH场景：
Valve=1.5在已测阈值中Relay最高（196）；若按R+T最差则为1.1（417），不是1.5。
这一选择属于有利参数对照，不证明1.5是40000笔、1.5 ETH场景最差值。
无论排名如何都报告两组Valve及完整成本，不能仅展示有利组来宣称EWMA机制优越。
旧Valve=3.0数据留在旧输出中不删除，独立15组config.valve_sweep.yaml仍保留。
硬窗口token显式hl=0；若日志显示EWMA=0，表示关闭EWMA而使用硬窗口6块。

原理参考：[NIST指数平滑](https://www.itl.nist.gov/div898/handbook/pmc/section4/pmc431.htm)。
这些具体数值是基于本项目的候选假设，不是文献给出的最优值。
在同一trace上反复调参属于探索，仍需独立时间窗口验证泛化，参见
[参数选择偏差说明](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html)。

在项目根目录运行（PowerShell）：

```powershell
python experiments/exp008_tdr_schedule_final/run.py
```

若已经进入 experiments/exp008_tdr_schedule_final 目录，只运行：

```powershell
python run.py
```

输出为 out/<时间戳>/report.json 和各 session 的 summary.json。V2/V3采用
按场配对的改善率/损失率；两轮仅供初步评估，不足以证明普遍优越性。
评估须同时比较六组的Relay、TDR事件、TDR transfers及Relay+transfers；
低Relay不意味着总开销最低。V2只检验相对Plain，不代表胜过所有对照。
部分交易金额超过初始余额，会引入支付能力压力；当前trace也未必具有可预测热点。
此前审查发现的路由余额时序、跨分片观测时钟等风险，此次只调配置，没有修复。
不要与占用相同Anvil端口的实验10同时运行。

修改前 config.yaml 和本README备份于项目根目录：
`.codex_backups/exp008_selected_contrast_20260926_135330/`。
要恢复，在项目根目录运行（覆盖当前这两个配置/说明文件，不删除结果）：

```powershell
Copy-Item .codex_backups/exp008_selected_contrast_20260926_135330/config.yaml experiments/exp008_tdr_schedule_final/config.yaml
Copy-Item .codex_backups/exp008_selected_contrast_20260926_135330/README.md experiments/exp008_tdr_schedule_final/README.md
```

## 历史设计与绘图说明（以下参数、五轮命令不代表当前默认值）

**测什么**：把 exp003 探索定稿的调度方案固化为可一键复现的生产实验，
与三个对照方案同场多轮对比，产出论文图表数据。

**定稿方案**：topup + EWMA（只补缺口 + 需求指数平滑）。
参数：ε=0.95、半衰期 hl=20 块、q_min=0.10、χ=10 块。
全本地执行：只用链上数据与 broker 自有资金，无外部提示。
设计全文：[../exp003_tdr_on_off/DESIGN_topup_ewma_CN.md](../exp003_tdr_on_off/DESIGN_topup_ewma_CN.md)。
探索史（含全部失败）：[../exp003_tdr_on_off/EXPLORATION_log_CN.md](../exp003_tdr_on_off/EXPLORATION_log_CN.md)。

**同场五方案**（同一负载、同一 seed、只换处理）：
- `plain@3`：不搬钱（基线）。
- `valve@3@1.3`：傻瓜水位阀（消融对照，无需求预测）。
- `tdr@3@@0.1`：原论文式 proportional（需求比例 + 全量重排）。
- `topup@3@@0.1`：topup 硬窗口版（只补缺口，无平滑）。
- `topup@3@@0.95@@20`：**定稿方案**（topup + EWMA）。

**负载**：新 trace 中按原始到达顺序选取 40000 笔跨片 CTX，16 分片、
每个 broker 每分片 3 ETH。`value_cap_eth=3`，保证任意单笔交易在初始状态
下可支付，避免把“单笔金额大于初始余额”误判为流动性失衡。
注入 rate=120（本实验复用 exp003 管线，已有完整历史运行；不能把 exp007 动态路由
在 120 档中止的结果直接套到此管线）。

**判据**（`report.json → verdicts`）：
- V1 每场每方案完整性、守恒、coordinator 预算及 mint RPC 校验全 True。
- V2 定稿方案 relay 中位数相对同场 plain 至少降低 20%。
- V3 定稿方案吞吐中位数相对 plain 损失不超过 15%。

**为什么多场**：单场 relay 噪声实测 2.5 倍（档案 §9/§16）。
论文引用跨场中位与区间。默认 sessions=5，路由种子依次为 7/17/27/37/47；
同一场内所有方案共享同一 seed，保证公平。

**运行**：
```bash
python -m brokerlab doctor --config experiments/exp008_tdr_schedule_final/config.yaml
python run.py                                        # 正式生产（默认 5 场）
python run.py --set exp.exp008_sessions=1            # 单场快验
python run.py --set exp.ctx_per_broker=4 --set chain.num_shards=4 \
  --set exp.exp008_sessions=1                        # 冒烟
```

**产物**：`out/<ts>/session_k/…`（每场完整 exp003 管线产物）；
`out/<ts>/report.json`（跨场聚合：每方案 relay/次数/搬量/段占比的中位与极值）。

**消融图（比原稿多出的一张）**：`plot_ablation.py` →
`figs/exp008_ablation.png`。阶梯式消融六档（同负载逐级加一个组件）：
不搬 → 搬运(均匀目标) → 需求目标(全量重排) → 只补缺口 → 深死区 → 需求平滑(定稿)。
面板 (a)relay 用对数轴，(b)搬运次数和 (c)搬量用可显示零值的对称对数轴；
(d)死区 ε 灵敏度含 ε→1 悬崖
（矩阵档 EWMA 口径曲线）。依赖一次消融补跑：
```bash
python run.py --set exp.exp008_sessions=1 \
  --set exp.arms="topup@3@@0.95,topup@3@@0.1@@20,topup@3@@0.95@@20"
python plot_ablation.py
```

新图的方案标签为 `TDR_OFF`、`UNIFORM`、`PROPORTIONAL`、`TOP_UP`、
`TOP_UP + BAND`、`TDR_FINAL`。绘图只选通过验收的 40k/120 正式报告和深死区
补充报告，不再把不同报告的极值当成额外样本；后者只有 1 场，比较时须注明。
图 (b)(c) 用可显示真实零值的对称对数轴；图 (d) 明确标出来自 10000 笔探索档。
旧版第 8 分片资金图只适用于历史热点 trace，**不是新 trace 的实际账户余额**。


**效度声明**：
① 复用 exp003 管线，判据与 M4 验收同源，无新机制代码。
② 新 trace 较均衡，TDR 的提升可能小于历史热点 trace；这是实验结论而非故障。
③ ε=0.95 距失效悬崖（1.0）0.05，鲁棒性依赖需求不换向（未测）。
④ 吞吐只作次级证据：rate120 已近机器平台，与方案无关。

**历史结果（旧热点 trace、150 ETH；不可与当前配置直接比较）**：
`out/20260910_033927/`（2026-09-10，sessions=2，
每方案 2 样本的跨场统计；V1/V2/V3 全 True）。

| 方案 | relay 中位 [min..max] | 搬运次数 | 搬量 ETH | TDR 段占比 |
|---|---|---|---|---|
| plain | 24848 [24835..24860] | 0 | 0 | 0 |
| valve | 20863 [20608..21117] | 273 | 13045 | 0.7% |
| proportional | 110 [107..112] | 22642 | 813408 | 57% |
| topup 硬窗 | 72 [61..83] | 17206 | 807759 | 43% |
| **定稿 topup EWMA** | **20 [8..32]** | **1227** | **163674** | **3.1%** |

定稿方案 vs proportional：relay 低 5.5 倍、次数省 95%、搬量省 80%。
搬量 163.7k 与物理下限 164k 几乎重合（V3）。
合并 exp003 既有 ε0.95 样本共 5 个：5/8/15/25/32，中位 15。
