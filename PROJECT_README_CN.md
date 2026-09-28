# exp_anvil_broker_qwen — BrokerChain/TDR 测试床（重构版）

Anvil 多分片上的 BrokerChain 流动性实验平台。`brokerlab` 是本项目内的 Python 包
（链管理/交易/协议机制/真实数据加载/绘图样式等共享底座）。
代码审核从 [CODE_READING_GUIDE_CN.md](CODE_READING_GUIDE_CN.md) 进入（逐文件阅读路线）。
**全部实验结果汇总在 [EXPERIMENT_REPORT_CN.md](EXPERIMENT_REPORT_CN.md)（exp001–exp008 实验报告）。**
设计与决策见 [PLAN_refactor_CN.md](PLAN_refactor_CN.md)；旧三代代码的缺陷证据见
[AUDIT_fatal_flaws_CN.md](AUDIT_fatal_flaws_CN.md)（三代原件已归档至 `../归档/exp_legacy_20260902/`）。

## 实验组织约定（一实验一包）

每个实验是 `experiments/` 下的独立文件夹，自含四件：

```
experiments/expNNN_短主题名/
├── README.md     几行说清：测什么、假设/判据、数据源、运行方法、产物、最近结果
├── config.yaml   全部参数 + 数据源配置（实验专属；--set k=v 可临时覆盖任意项）
├── run.py        独立启动脚本：编排执行 + 出图；退出码 = 判据是否全部成立
└── out/<时间戳>/  运行产物：CSV 源数据、summary.json、figs/*.png、logs/
```

运行：`cd experiments/expNNN_... && python run.py`（先 `python -m brokerlab doctor
--config experiments/expNNN_.../config.yaml` 体检）。已有实验：

| 编号 | 主题 | 一句话结论 |
|---|---|---|
| exp001_relay_vs_broker | 两机制的延迟/跨片通信/链上足迹对比 | 延迟相等、relay 通信 ~20×、链上足迹对称 |
| exp002_broker_drain_no_tdr | 无 TDR 真实数据耗尽基线（对照手稿 fig1） | 手稿 C1–C4 全部复现一致 |
| exp004_b2e_revenue | B2E 手续费收入与三条守恒恒等式（off/on 对照） | 恒等式全 True；收入=ΣβF 逐 broker 精确；费用不改变路由序列 |
| exp005_broker_substrate_benchmark | 50 broker 流水线引擎跑在串行/线程/进程上（M3 前置） | 线程 0.82×（无增益且更差）、进程 3.8×：每 broker 一进程被实证 |
| exp006_rate_invariance | 50 broker × 4 分片 × 1 万笔，按逻辑块扫注入速率 | 决策/守恒跨 25→300 CTX/块逐位不变（审计 F1 根治）；机器上限 ≈120 CTX/s |
| exp007_dynamic_routing | 动态路由（coordinator 实时选 broker）vs 静态指派，同流双臂两轮 | declared 回归门 PASS；终审改判 27% 下守恒零损；动态吞吐 = 静态的 0.44–0.62（coordinator 单点 = M4 TDR 的靶） |
| exp003_tdr_on_off | TDR 开/关对照（M4 验收）+ 调度策略全探索 | 改判 27%→0.5% 起步；探索定稿 topup+EWMA（ε0.95）：40000 笔正式档 relay 0.04-0.06%、搬量≈物理下限、次数比原 proportional 省 94%（详见其 EXPLORATION/DESIGN 文档） |
| exp008_tdr_schedule_final | 定稿方案正式评估：五方案同场 × 多场，跨场聚合出论文数据 | 复用 exp003 管线，判据 V1-V3（gates/relay 中位≤50/搬量≈下限±10%）；产物 report.json |

## 安装（Linux / 实验室机器移植通用）

```bash
# 1) 依赖
python -m venv .venv && source .venv/bin/activate    # 或直接用 conda env
pip install -r requirements.txt && pip install -e .

# 2) anvil（Foundry）；代码按 ~/.foundry/bin/anvil → PATH 顺序自动发现
# curl -L https://foundry.paradigm.xyz | bash && foundryup

# 3) 体检（迁移后第一步）
python -m brokerlab doctor --config configs/smoke.yaml
```

真实流量数据：`trace/ETH_cleaned.csv`（328 MB，已入项目）。`traffic.real_csv_path: null`
即默认读此路径；相对路径一律按项目根解析。迁移实验室机器 = `git clone` + `rsync trace/`，
零配置改动。

## M1：BrokerChain 基座演示（真实跨片转账）

```bash
python -m brokerlab demo --config configs/smoke.yaml
```

做且仅做一件事，但每一步都是真实的：
1. 启动 2 个 anvil 分片（独立 chainId，1s 块）；
2. 从 ETH 主网真实交易中流式筛选 2 笔跨片交易（地址→分片/账户映射与三代代码同规则）；
3. 一笔走 **broker 路径**（Θ1 sender→broker@src，Θ2 broker→receiver@dst），一笔走 **relay 兜底路径**（Θ1 sender→BURN@src，Θ2 coordinator→receiver@dst；burn-and-mint 语义，coordinator 段 = mint 签发段，消耗的是与销毁额一一对应的铸造预算，不是流动性）——全部是真实签名、真实挖矿、有回执的交易；
4. 按 (地址, 分片) 逐点核对 8 项余额差分（gasPrice=0 → 精确守恒），写 `results/<run>/report.json`。

`setBalance` 只出现在 setup 充值阶段（设计规则），实验过程中不出现。

## M1.6：broker 资金耗尽基线（→ exp002_broker_drain_no_tdr）

```bash
cd experiments/exp002_broker_drain_no_tdr && python run.py
```

2026-09-04 结果（匹配器接入后正式跑）：真实 trace 多数方向流 150 笔（合计 436 ETH），
150/150 成功执行、0 失败，burn≡mint 对账精确，broker 总额漂移 0 wei。手稿四结论**全部一致**：

| 结论 | 手稿预期 | 实测（执行序口径，跨跑稳定） |
|---|---|---|
| C1 目的分片趋零、总资金守恒 | fig1b | dst 终值 **0.0013 ETH**（初值 100）；src 涨至 200，总额零漂移 |
| C2 relay 回退出现后占比递增 | fig1a | 四分位份额 **5% → 84% → 97% → 100%**（首次回退 idx=34，三跑逐位一致） |
| C3 跨片数据量随之上升 | fig1a 红线 | 每笔平均 **202 → 2009 B（9.9×）**（M1.5 实测口径） |
| C4 加大充值只推迟不消除 | Introduction | 首次回退 idx **34 → 103**（充值 ×3 ⇒ 回退点 ×3.03，几乎精确线性），终值仍 **0.0165 ETH**；块号类数字跨跑 ±40% 抖动，故不引用 |

图：exp002 `out/<ts>/figs/drain_vs_manuscript.png`（与手稿 fig1 同构）。
注意口径：本实验是单进程顺序基线（机制层验证，无负载压力）；TDR 的对照效果在 M4 用同一耗尽场景检验。

## M1.5：relay vs broker 机制对比实验（→ exp001_relay_vs_broker）

```bash
cd experiments/exp001_relay_vs_broker && python run.py   # 同一批真实 trace 行，双路径各跑一次
```

2026-09-04 结果（匹配器接入后正式跑，50 对 × 2 路径 = 100 笔全成功，全局净和 0 wei）：
**H1** 延迟相等：broker e2e 1.09s vs relay 1.12s（相对差 2.5%，两路径 hops≈2.0）；秒级均值跨跑抖动，取重复均值；
**H2** relay 跨片通信 2005 B/CTX（Θ1 RLP + 回执证明代理，实测）vs broker 100 B
控制消息常数 ≈ 20×（换成 RLP 证明下界仍 3–5×）；字节类指标三跑逐位稳定；
**H3** 链上足迹对称：各 100 段、每分片字节数 5383/5382——relay 的额外开销在跨片
消息而非链上，与 BrokerChain 论文口径一致。附带流动性账：broker 路径垫付流动性、
目的分片净 drain，relay 路径 broker 分文未动。
产物：exp001 `out/20260904_024203/`（ctx_rows.csv + summary.json + figs/exp001_overview.png）。

## 测试

```bash
pytest tests/ -q          # 纯函数测试，无需 anvil（含 TIME_WAIT 端口探针回归）
```

## 配置

`configs/*.yaml` → frozen dataclass；命令行临时覆盖用 `--set section.field=value`
（如 `--set tdr.window_blocks=20 --set tdr.enabled=true`）。每次运行把解析后的完整
配置快照写进 run 目录（`params.json`）。

| profile | 规模 | 用途 |
|---|---|---|
| smoke.yaml | 2 分片 × 1 broker | 当前：M1 演示；后续回归 |
| medium.yaml  | 4 × 8 | 流水线与 TDR 开/关对比（M3+） |
| full.yaml    | 16 × 50, 12s 块 | 论文口径，跑在实验室机器（M7+） |

## 进度

- [x] M0 旧代码归档
- [x] M1 BrokerChain 基座：真实数据单 CTX 跨片转账 + relay 兜底 + doctor
- [x] M1.5 relay vs broker 机制对比实验（延迟/跨片通信/链上足迹三假设全验证）
- [x] M1.6 broker 资金耗尽基线：真实数据复现手稿 Experiment A，C1–C4 全部一致
- [x] exp005 执行基底基准（2026-09-04 用户重排，先于 M3）：流水线 tick 引擎 + 50 broker
      三容器对比——进程 3.8×、线程 0.82×，"每 broker 一进程"由数据裁定成立
- [x] exp004 B2E 手续费：机制层（brokerchain 费用参数）+ 三恒等式 + off/on 对照，全过；
      exp002 关闭态回归逐位一致（确定性列）
- [x] exp006 注入速率不变性 + 吞吐上限（50 broker × 4 分片 × 1 万笔）：决策指纹与守恒跨
      25→300 CTX/块逐位一致（逻辑时钟根治审计 F1），机器平台 ≈120 CTX/s、RSS ≈3.9 GB
- [x] exp007 动态路由（M3 核心上线，用户 2026-09-06 裁定"路由必须实时"）：coordinator
      块循环 + 账本终审 + relay 代铸；declared 回归门 PASS；动态代价 = 0.44–0.62×静态（真结论）
- [x] M2 TDR 策略纯函数 + 单测（镜像 Go 参考实现，16 项含 Go 交叉样例全绿）——
      完成（2026-09-07，证据=tests/test_tdr_policy.py）
- [x] M3 多进程块流水线 + 逻辑时钟 + 账本 —— 验收通过（2026-09-07，证据=exp007：
      两轮全臂守恒、declared 跨跑一致、看门狗可中止；动态匹配/终审/代铸链路已可运行）
- [x] M4 TDR 接入（真实再平衡交易）—— 完成（2026-09-07，证据=exp003：
      tdr_engine 状态机 + 两半段真实转账 + coordinator 代铸；773 事件 0 失 0 拒答，
      改判 27%→0.5%、守恒门全过；验收见 exp003 README）
- [ ] M5 blockscan 容量度量
- [ ] M6 基线 vs TDR 对比（exp003 双臂已就位；η/容量占比口径在 M5 后定稿）
- [ ] M7 实验室机器 full 档验证
