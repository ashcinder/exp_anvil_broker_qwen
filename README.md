# BrokerChain/TDR 分片实验平台

本项目是一个基于 Python、Web3 和 Anvil 的分片区块链实验平台，用于研究跨分片交易（CTX）、broker 流动性、relay 回退、TDR 余额再平衡、EWMA 需求估计、地址映射集中度以及方向性交易负载。

项目包含统一的实验底座、18 组逐步演进的实验、配置文件、自动化测试和中文设计文档。原始 Trace 数据和实验运行结果体积较大，不纳入 Git 仓库。

## 主要研究问题

跨分片交易通常需要 broker 在目的分片预先垫付资产。当交易长期偏向某些分片或方向时，部分 broker 子账户会快速耗尽，从而导致 relay 回退、等待时间增长或交易失败。本项目主要研究：

1. broker 余额耗尽和 relay 回退如何产生；
2. TDR 是否能够安全、及时地在分片间重新分配余额；
3. Hard Top-up、Proportional、Valve 和 EWMA 等策略的性能与成本差异；
4. 地址集中到一个或多个热点分片后，容量压力和余额压力如何变化；
5. 热点交易占比与 A→B 方向比例能否独立控制，以及二者是否存在交互作用。

## 核心概念

- **CTX**：跨分片交易。发送方和接收方位于不同分片。
- **Broker 路径**：发送方先向源分片 broker 转账，再由目的分片 broker 向接收方垫付。
- **Relay 路径**：broker 流动性不足时使用的兜底路径，采用 burn-and-mint 语义完成跨片结算。
- **TDR**：分片间余额转移与再平衡机制。它根据各分片的资金需求执行真实 burn/mint 转移。
- **EWMA**：指数加权移动平均，用于平滑近期需求并预测各分片所需的安全余额。
- **热点分片**：承担显著高于普通分片交易份额的分片。
- **方向性负载**：交易在两个分片间存在明显的净流向，例如大量交易由 A 流向 B。

需要特别注意：TDR 的“触发判断”“生成事件”和“完成实际转移”是三个不同口径，分析结果时不能把它们混为一个计数。

## 实验 9—18 概览

实验 9—18 从机制验证逐步发展到双因素受控实验。完整设计、参数含义、有效性威胁和分析方法见 [实验 9—18 设计思路](EXPERIMENTS_09_18_DESIGN_CN.md)。

| 实验 | 主题 | 主要目的 |
| --- | --- | --- |
| 9 | 双分片 TDR 开关 | 在最小环境中验证 TDR 是否真实改变余额并满足守恒 |
| 10 | TDR/EWMA 参数搜索 | 搜索窗口、阈值、半衰期和 epsilon 等候选参数 |
| 11 | Clean 数据复验 | 在清洗后的真实交易数据上验证候选配置的稳健性 |
| 12 | 地址映射集中度 | 研究地址向单热点或双热点分片集中后的容量与余额压力 |
| 13 | 集中场景参数搜索 | 在多种热点布局下进行粗搜、精炼和留出验证 |
| 14 | 天然方向性窗口 | 从真实 Trace 中选择低方向性和高方向性时间窗口 |
| 15 | 学习型方向布局 | 根据训练期账户收发行为学习接收型账户并进行静态映射 |
| 16 | 方向性分层采样 | 在固定映射下控制流入目标分片的真实交易比例 |
| 17 | 控制位方向负载 | 固定交易集合，通过确定性控制位改变部分交易的流向 |
| 18 | 双热点双因素实验 | 独立控制热点 CTX 占比 `alpha` 和 A→B 占比 `beta` |

实验 18 每个小实验固定使用 10,000 笔 CTX：

- `alpha ∈ {0.25, 0.50, 0.75, 1.00}`：由热点分片 A/B 处理的 CTX 占比；
- `beta ∈ {0.40, 0.50, 0.60, 0.70, 0.80, 0.90}`：热点 CTX 中 A→B 的占比；
- 另设自然后缀映射基线和 `alpha=0` 的隔离背景基线；
- 共 26 个场景，每个场景比较 5 种方法。

这里控制的是热点交易数量，而不是唯一热点账户数量。方向比例使用确定性排序和定额切分实现，因此只有四舍五入误差，不依赖有限样本的随机抛硬币结果。

## 项目结构

```text
brokerlab/                         公共实验底座
├── broker_engine.py               broker 执行与调度
├── chain.py                       Anvil 分片生命周期管理
├── controlled_direction.py        实验 17 的方向控制
├── mapped_workload.py             地址映射与热点负载
├── tdr_engine.py                  TDR 执行状态机
├── tdr_policy.py                  TDR/Top-up/EWMA 策略
└── two_shard_factorial.py         实验 18 双因素负载生成

experiments/                       独立实验目录
├── exp001_... ～ exp008_...       基础机制与前置实验
└── exp009_... ～ exp018_...       本轮重点实验

configs/                           公共配置
tests/                             自动化测试
trace/                             本地原始数据，不进入 Git
results/                           通用运行结果，不进入 Git
EXPERIMENTS_09_18_DESIGN_CN.md     实验 9—18 设计文档
PROJECT_README_CN.md               原详细项目说明
```

每个实验目录通常包含：

```text
experiments/expNNN_实验名称/
├── README.md                      实验目标、假设和运行说明
├── config.yaml                    实验参数
├── run.py                         独立运行入口
└── out/<时间戳>/                  运行结果，不进入 Git
```

## 环境要求

- Python 3.11 或更高版本；
- Foundry/Anvil；
- 推荐 Linux 或 WSL；Windows 也可以运行测试和部分实验；
- 真实交易 Trace 数据需要单独准备。

## 安装

Linux/macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Windows PowerShell：

```powershell
python -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

安装 Foundry/Anvil 后，先运行环境体检：

```bash
python -m brokerlab doctor --config configs/smoke.yaml
```

## Trace 数据

原始 Trace 不上传到 GitHub。请在项目根目录创建 `trace/`，并根据实验配置放入对应 CSV，例如：

```text
trace/ETH_cleaned.csv
```

配置中的相对路径以项目根目录为基准。迁移到其他机器时，应通过可信的文件传输方式单独复制 Trace，而不是提交到 Git。

## 运行实验

通用运行方式：

```bash
cd experiments/exp018_two_shard_factorial
python run.py
```

也可以直接从项目根目录运行：

```bash
python experiments/exp018_two_shard_factorial/run.py
```

运行前建议先检查对应实验目录中的 `README.md` 和 `config.yaml`。若实验脚本支持配置覆盖，可使用 `--set section.field=value` 临时修改参数，而不必改动配置文件。

实验 18 默认会运行完整的 `alpha × beta` 因子组合。正式运行前建议先减少交易数量或场景数进行 smoke 测试，确认 Anvil、端口、Trace 和依赖均正常。

## 输出与结果管理

实验输出通常写入实验目录的 `out/<时间戳>/`，包括：

- 解析后的完整配置；
- CTX 逐笔记录；
- broker 余额快照；
- TDR 事件和实际转移记录；
- 汇总指标与图表；
- 运行日志和进程信息。

仓库的 `.gitignore` 已排除以下内容：

- `trace/` 原始数据；
- `results/` 和所有 `out*` 实验结果；
- 校准结果与生成图表；
- 虚拟环境、缓存、临时目录和日志。

提交代码前仍建议执行 `git status`，确认没有大型数据文件或实验结果被意外加入。

## 测试

纯函数和核心逻辑测试不需要启动 Anvil：

```bash
pytest tests/ -q
```

若直接执行 `pytest` 扫描整个项目，历史备份或本地临时目录可能被 pytest 误收集，因此推荐明确指定 `tests/`。

## 主要评价指标

- CTX 完成率与吞吐量；
- 平均值、中位数和尾部等待时间；
- broker 路径与 relay 路径占比；
- 各分片余额轨迹、最低余额和低余额持续时间；
- TDR 触发事件数、实际转移次数、总金额和平均金额；
- 区块 gas 使用率与满载率；
- 热点交易占比、方向比例、净流量和 HHI；
- burn/mint、broker 总余额和账本守恒；
- 失败交易、超时和事件—转移不一致情况。

## 复现实验时的注意事项

1. 固定配置、随机种子、代码版本和 Trace 筛选区间；
2. 先检查目标热点占比和方向比例是否实现，再解释性能变化；
3. 参数搜索与最终验证应使用不同区间或独立数据；
4. 重要结论应使用多个随机种子或 session；
5. 明确区分自然映射、学习型静态映射、采样控制和逐交易合成路由；
6. 单次实验结果不能代替置信区间、重复试验分布或跨数据集验证。

## 相关文档

- [实验 9—18 设计思路](EXPERIMENTS_09_18_DESIGN_CN.md)
- [项目原详细说明](PROJECT_README_CN.md)
- [代码阅读指南](CODE_READING_GUIDE_CN.md)
- [实验总体设计](EXPERIMENT_DESIGN_CN.md)
- [早期实验报告](EXPERIMENT_REPORT_CN.md)
- [重构计划](PLAN_refactor_CN.md)
- [历史缺陷审计](AUDIT_fatal_flaws_CN.md)

## 安全说明

不要将 GitHub Token、私钥、账户密码、Trace 数据或实验机配置提交到仓库。若凭据曾出现在聊天记录、终端输出或日志中，应立即撤销并重新生成。
