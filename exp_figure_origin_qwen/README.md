# exp_figure_origin_qwen — 用重构实验数据重绘手稿实验图

对 `../exp_figure_origin/`（手稿实验图原始绘图代码+数据）做三件事：

1. **覆盖度对比**：原始 8 幅图所需数据 vs 重构实验（exp001–exp008）产物，见下方矩阵；
2. **可新增图清单**：重构实验已有、但手稿没画的数据；
3. **重绘**：每图目录内 = 绘图脚本（`plot_<目录名>.py`，原始脚本的忠实移植，
   配色/尺寸/文字/标注逻辑**逐字照抄**，仅按下面「与原始脚本的差异」调整）+
   **该脚本用到的全部数据 CSV，就放在脚本旁边**（原始文件名、原始目录布局，
   如 `fig6a_threshold/ENABLE_TDR=1_TDR_THRESHOLD=.../broker_pnl.csv`）+ 产物 `out/`。
   数据由 `prepare_data.py` 从重构实验产物换算生成。

运行：`bash run_all.sh`（先换算数据，再逐图出 PDF/PNG 到各自 `out/`）。
原始目录 `../exp_figure_origin/` 一字未动。

## 与原始脚本的差异（全部显式声明，不含任何隐改）

1. 输入/输出路径与脚本命名（`test.py` → `plot_*.py`；CSV 与脚本同目录）；
   强制 `Agg` 后端 + 附加同名 PNG 预览（PDF 文件名与原版一致）；
2. **轴范围自适应**（仅绝对数值坐标框，因旧 mock 尺度与 40000 笔正式档冲突）：
   fig5b（两面板 y 框、on 面板 x 域、放大窗、off 的 '1e18' 角标删除）、
   fig6a（左右 y 框）、fig6b（`CTX_COUNT` 字面量 50000→40000）；
   配色/字号/线型/图例/标注偏移/人工剔群值规则全部未动；
3. fig6a/6b 的 run 目录 `CTX_COUNT` 按各场真实笔数命名（40000）。

---

## 一、覆盖度矩阵（问题 1 的答案）

图号 | 原始输入 | 重构侧来源 | 状态 | 缺口/说明
---|---|---|---|---
fig1 动机 | `exp_b_handler_trace_ori.csv`（每块 broker/relay 计数 + A/B 余额） | exp008 正式档 `plain@150.0/ctx_rows.csv` 换算（每块计数、按事件重建各 broker 子账户余额，A/B 线 = 全体 broker 最大/最小子账户之和） | ✅ 可绘 | 原图右轴 1e22 wei 尺度系旧 mock；本重绘按真实资金量自然落幅（0.75→0 抽干曲线可见）
fig5a 传输量 | `exp_b_tdr_trace_2.csv`（每块计数） | exp008 `topup@150.0@0.95@20.0`（定稿方案）换算 | ⚠ 可绘，但**原脚本本身就含合成覆盖段**（`ref_broker_ctx`、`seed(42)`、`relay[3]=33`、`relay[5]=98`、前 3 bin 强制 0/真实混合），第 4–10 根柱不是实验数据。移植版原样保留该逻辑，真实数据只进前 3 bin
fig5b 余额 | `tdr_on/off_balance_distribution.csv`（每 epoch：normal 确认数、shard_0/1 余额、shard_1_tau、shard_1_upper_bound、tdr_self_transfer_confirmed） | 余额/计数：由 ctx_rows+tdr_moves 事件重建；**τ/上界/事件时刻重构实验未落盘** → 用 `brokerlab.tdr_policy`（与运行时同一套纯函数）离线重放得到，搬运总数与 `tdr_moves.csv` 真值对照打印（本次重放 583 vs 真实 480，重放偏多——重放按块即时结清、无运行时确认时延/在途约束，触发略密） | ⚠ 半覆盖 | 数据源 = **40000 笔正式档**：off = exp008 `plain@150.0`（累计正常确认 15165，恰好铺满原图 0–15000 x 域）；on = `tdr@150.0@0.1`（proportional、ε=0.1 论文默认——与手稿原图同族，红点密集）。唯一遗留记录缺口 = 每 epoch 策略快照未落盘（建议 exp003/008 增加 balance/τ 时序导出）。**轴自适应声明**：原脚本 y 框（0–0.8 / 0–5 ETH）绑定旧 mock 的 0.25 ETH 池，正式档余额在 0–数百 ETH，故 port 中仅将 y 上限/刻度与 on 面板 x 域改为按数据 5 格取整，off 面板 x 保持原样；配色/字号/线型/图例/标注偏移全部逐字未动。`calib_fig5b/` 是早期定标尝试（小池子让数值适配旧框但笔数不足），已被正式档方案取代，仅留档
fig5c 收益 | `tdr_on/off_brief_info.csv`（每 epoch：net_system_revenue、total_tdr_gas_cost、broker_ctx、relay_ctx） | 全量档（exp003/008 多进程管线）**不支持 B2E 手续费记账**（`broker_engine.py` 无 fee 路径，链 gasPrice=0）；唯一收入数据 = exp004（60 笔机制级） | ❌ 有记录缺口 | 重绘版曲线压在画幅原点（0.0105 ETH vs 15 ETH 轴），已如实渲染作证据。**已补齐（2026-09-11）**：① B2E 记账已接入多进程管线（`brokerlab/broker_engine.py`：
Θ1a 含费 v+F、Θ1b broker@src 小额烧币（本地 nonce 域，避开 coordinator 预分配的
sender 域；各账户终态与 brokerchain 串行版逐元一致）；relay burn=v+F、mint=v；
费用腿开启时 ctx_rows 追加 fee_broker_wei/fee_burn_wei 两列，默认关 ⇒ schema 逐字节不变）；
② 带费 TDR off/on 对照跑 = `fees_fig5c/`（driver3 自动执行）。跑完本图自动换正式数据源
fig5d 热点 | `tdr_on/off/ctx_records.csv`（status、source_shard、confirm_time） | exp008 两臂 ctx_rows 换算（confirm_time=块高秒，1s 块） | ✅ 可绘 | 定稿方案使 With-TDR 侧 η 几乎全蓝，对照比原图（旧 proportional）更强，属改进非失真
fig6a 阈值扫描 | 每 run 一目录 `broker_pnl.csv`（total_rebalance_transfers、relay_fallback_count ×50 broker） | **40000 笔正式档** proportional(tdr) ε 臂：自动扫描 exp008/现成 exp003 的 16 分片场 + `sweep_fig6a/` 补跑场（统一 120 CTX/s、num_shards==16 护栏） | ✅ 可绘 | 原图=6ε×10run。现每 ε 2–5 场（首批 4 分片误扫已删，见附录 A 警告）。注意：`whis=(0,100)` 下单 run 的箱会塌成零高度不可见，故每 ε 至少补到 2 场（同 seed 亦可——TDR 结清/改判受真实时延影响，跨场数字确有散布）。脚本里的**人工剔群值规则**（TRANSFER_REMOVED 等具体旧数值）逐字保留、对新数据空转；左右 y 框由 0–10000/0–8000 改为按数据 5 格自适应（40k 档每场 transfer ≈2.2万，超旧框），sci 记法/字号/颜色/箱样式未动。更多 run 用附录 A 命令续跑，`prepare_data` 自动纳入
fig6b 窗口扫描 | `results/…TDR_WINDOW=…_run=…/broker_pnl.csv` | **已绘**：`sweep_fig6b/w{5..50}/` window 5–50 × 2 场（16 分片、40000 笔、ε=0.2） | ✅ | 左右 y 框按数据自适应（小窗口每场 transfer 达 2.5 万超旧 mock 框），其余样式未动；`CTX_COUNT` 字面量按真实 40000
fig6c 装备比例 | 数字重复目录 ×`pct{10..100}` 实验（exp_e_system_summary.json、tdr_broker_revenue.csv） | **旋钮已加**：arms token 第 8 字段 `@frac`（`run.py::_arm_spec/run_arm`，只给抽中比例的 broker 装 tdr 引擎，其余 plain，同流同 seed）；扫描产物 `frac_fig6c/p{10..100}/`（driver3 自动执行），`prepare_data.prep_fig6c()` 换算成原版三件套 | ✅ 已绘 | 左轴利润=ΣβF（费用腿产物）；η=broker 服务占比；frac=100% 即全员对照点。两指标随装备比例单调爬升（η 20%→96%、利润 5.1→8.1 ETH），20000 笔/场统一口径 |

**小结（2026-09-13 终态）**：8 幅图全部重绘成功。曾有过的三个卡点均已解除：
① B2E 费用腿已进多进程管线（Θ1a 含费 + Θ1b broker 烧币，关费逐字节回归旧路径，
   90 项单测全绿）→ fig5c/fig6c 左轴有真实收入；② arms token 新增 `@frac`
   装备比例旋钮 → fig6c 可扫；③ 6a/6b/6c 的多 run 扫描经 driver3/4（串行、
   端口预检、num_shards/rate 护栏、可续跑）跑齐。
fig5b 的 τ/事件时刻仍是离线重放（重放 583 vs 真值 480 对照打印）；
唯一遗留建议 = 运行时落盘每 epoch 的 (τ, upper, balance) 快照，可去掉重放。

## 二、可新增的图（问题 2 的答案）

重构实验已产生、手稿未画的数据，够格新开这些图（沿用同一套样式底座即可）：

- **机制对比图**（exp001）：broker vs relay 的 e2e 延迟 ECDF、每 CTX 跨片字节
  （100 B vs ≈2005 B，≈20×）、链上足迹对称性柱状图；
- **耗尽基线图**（exp002）：单方向流下 dst 子账户抽干 + 首次回退点随充值线性右移
  （C1–C4 判据图，已有 `drain_vs_manuscript.png` 雏形）；
- **五方案同台对比**（exp008 `report.json`）：各方案 relay 中位数[min..max]、
  搬运总量 vs 物理下限、TDR legs 占比、吞吐——柱状/箱式组图（论文 M6 主图候选）；
- **速率不变性与吞吐上限**（exp006）：决策指纹逐位一致的“不变性带”图、
  CTX/s vs 注入率曲线（≈120 上限）、RSS 曲线；
- **执行基底基准**（exp005）：serial/thread/process 三容器每-tick 耗时与加速比
  （线程 0.82×、进程 3.8×，支撑“每 broker 一进程”设计决策）；
- **动态 vs 静态路由**（exp007）：改判率、守恒校验、coordinator 单点瓶颈耗时分解；
- **守恒审计图**：burn≡mint 台账、global net=0、账本 vs 链上逐格核对的
  mismatch 热图（G3 判据的可视化）；
- **搬运量分布**：`tdr_moves.csv` 的 amount 分布 ×方案（对物理下限的贴合度）；
- **η 热图家族**（fig5d 同源）：valve/base/topup@0.1 各臂的 shard×time η 面板，
  作为消融附录图。

## 三、重绘结果与逐图注意（问题 3 的答案）

已产出（各 `*/out/` 下 PDF+PNG），全部统一 **40000 笔注入口径**（fig5c 除外，其收入数据只有 exp004 机制级）：
fig1 ✅（exp008 plain 臂全流）；fig5a ✅（定稿 topup 臂前 600 块；第 4–10 柱为原脚本自带合成段）；
fig5b ✅（off=plain、on=tdr@0.1 正式档；y 轴/x(on) 轴按数据自适应，声明见矩阵行；
红点=策略重放事件，重放/真实搬运数对照打印在 prepare 日志）；
fig5c ⚠（数据存在但压在原点——缺口①）；fig5d ✅（全 40000×2 臂热图）；
fig6a ✅（6ε×2–5 场、统一 40000 笔/120 CTX/s 档，由 sweep_fig6a/ 补跑所得）；
fig6b ✅（window 5–50×2 场，transfer/relay 随窗口单调下降）；
fig6c ✅（装备比例 10–100%×2 轮、20000 笔/场带费，η 与总利润单调爬升）；



注意：
- **字体**：本机无 Calibri（matplotlib 回退 DejaVu）。出正式稿请在有
  Calibri（或等度量的 Carlito）的机器重跑，脚本里字体设置未动；
- **轴范围自适应清单**：配色/尺寸/文字/线型/图例/标注偏移/人工剔群值规则
  全部原封未动；仅以下“绝对数值轴框”因绑定旧 mock 尺度而改为按数据自适应
  （逐处标了 `[轴自适应]`/`[轴自适应①②]` 注释）：
  fig5b（两面板 y 框与 on 面板 x 域、放大窗位置/刻度、off 的 '1e18' 角标移除）、
  fig6a（左右 y 框）；
- 数据源 run 全部写死/可自动扫描（`prepare_data.py`），可溯源、可重跑。

## 附录 A：fig6a 扫描与后续补齐

**已跑完**（16 分片；经 driver3/4 修正串行执行，`DRIVER4_DONE` 标志；补跑直接复用其脚本）：
```bash
cd ../experiments/exp003_tdr_on_off
python run.py --out-root ../../exp_figure_origin_qwen/sweep_fig6a \
  --set chain.num_shards=16 \
  --set exp.arms="tdr@@@0.05,tdr@@@0.1,tdr@@@0.2,tdr@@@0.3,tdr@@@0.4,tdr@@@0.5" \
  --set exp.ctx_per_broker=800 --set exp.pool_scan=200000 --set exp.rate=120 \
  --set exp.max_backlog=20000 --set exp.drain_tail_s=900     # 40000 笔 × 6ε
```
⚠ **配置漂移警告（重要）**：`exp003/config.yaml` 当前默认 `num_shards: 4`（medium 档），
与 exp008 正式档的 16 分片底座不同。首批补跑忘了覆盖，整批 4 分片场（transfer ≈6k，
16 分片同参数 ≈21-23k）已删除重扫。**用 exp003 跑 40k 正式档口径必须显式
`--set chain.num_shards=16`**；`prepare_data._scan_6a_runs()` 已加 num_shards==16 护栏。
6c 用 20000 笔/场统一口径（driver4：混合舰队收口预算 tail=1500；40000 笔版在
部分装备下会超时）。`bash run_all.sh` 全量出图。
每 ε 要凑满 10 run：把上面的命令循环若干场即可（同 seed 也行——TDR 结清受真实
时延影响，跨场散布是实的），`prepare_data._scan_6a_runs()` 按时间序自动编号 run。

补跑中发现两个 exp003 配置边界（对 rate=120 的 40000 笔档）：
① `max_backlog` 默认 6000 会触发 coordinator 防积压中止（exp008 配的是 20000）；
② 收口预算 `N/rate + drain_tail_s + 120` 对搬运多的臂不够，需
`--set exp.drain_tail_s=900`；③ 窗口变密（小 window）时 coordinator 代铸积压，
`max_backlog=20000` 会中止，40000 笔档建议 `--set exp.max_backlog=45000`。
三条都只是时间/预算旋钮，不改机制。**统一驱动 = `sweep_fig6b/driver3.py`**：
可续跑（按“有效场数”补差）、串行、每场前端口预检、失败重试，依次跑完
6b（window 5–50 ×2 场）、6a（ε ×2 场）、5c 带费 off/on、6c frac 10%–100% ×2 场；
完成标志 `sweep_fig6b/DRIVER3_DONE`，之后 `bash run_all.sh` 一次出齐全部图。fig6b 同法：外层循环
`--set exp.tdr_window_blocks=$w`（w∈5/10/20/40/60）× run 循环（脚本端口
目前按原样匹配 `TDR_WINDOW=*_run=*` 目录名，届时在 prepare_data 里补一段
6b 扫描输出即可）。fig6c：需先在实验侧加“装备 TDR 的 broker 子集比例”旋钮
（每臂按比例分配引擎），之后同法扫 pct∈{10..100}。

## 附录 B（留档）：早期 fig5b 定标跑批——已被正式档方案取代

定标（2 分片×1 broker、0.12 ETH 池）能让旧 mock 轴框原样适配，但笔数只有几百，
不满足“总注入 40000”的口径，已弃用；产物留 `calib_fig5b/` 供查。
期间发现一个实验侧问题：proportional 臂在 1-broker 小拓扑收尾时
coordinator/engine 竞态（“引擎未落定”），两次复现 2/2。

```bash
cd ../experiments/exp003_tdr_on_off && python run.py \
  --out-root ../../exp_figure_origin_qwen/calib_fig5b \
  --set chain.num_shards=2 --set scale.num_brokers=1 \
  --set traffic.value_floor_eth=0.0001 --set traffic.value_cap_eth=0.0006 \
  --set exp.pool_scan=200000 --set exp.ctx_per_broker=700 \
  --set exp.group_cap_mult=100 --set exp.rate=80 --set exp.balances_eth=0.12 \
  --set exp.arms="plain,topup@@@0.95@@@@20"
```

垫资取 0.12 ETH/片：700 笔 ×均值 2.85e-4 ETH ≈ 0.2 ETH 方向流足以把目的子账户
抽干（触发终审回退 + topup 补货事件），同时 0.12 落在原图坐标框内。
（0.25 时目的侧只掉到 ~0.05，够不到 topup@0.95 的近空触发带 → 零事件；
定稿方案本就“只在近枯竭时回灌”，这本身也是策略行为，见 exp003 档案。）
