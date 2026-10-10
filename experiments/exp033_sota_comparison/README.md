# exp033 · SOTA 再平衡方案对照实验

**目的**：回答审稿人/指导老师的意见——"与 SOTA 方案的对比不足"。
把 TDR 的"**降 relay 且不造成过多额外搬运**"双重卖点，做成一张可归因、可反驳的
Pareto 图：横轴 relay CTX（越低越好），纵轴再平衡搬运次数（越低越好），
并画出过 plain 点的盈亏平衡射线簇。

设计与文献依据见仓库根的 `SOTA_COMPARISON_DESIGN.md`。

---

## 1 这个包做了什么

- **新增 7 条 SOTA 对照臂**（决策规则在 `brokerlab/sota_policies.py`）：
  `flexichain` / `loopmax` / `spider` / `pnps` / `autoloop` / `overpass_u` /
  `overpass_d` / `ucrb`（共 8 条，其中 overpass 两个 θ 变体）。
- **复用 exp025 的串行可恢复 runner 与 exp003 的多臂管线**（同链、同路由、
  同守恒门 G1–G4），不新造一套评测口径。
- **负载与 exp025 逐参数相同**：16 分片 × 50 broker × 40,000 CTX、
  route_seed 7/17/27、regime-shift 轮转负载（热点在分片 11/3 之间切换）、
  每 broker 每分片 45 ETH。所以本包的每个点都与已发表的 plain/TDR 点同负载可比。

## 2 臂清单：文献 → 决策规则 → 代码

共同的移植原则：所有方案的**多方协调机制**（环分解、MPC、HTLC、领导者选举、
Pedersen 承诺）在"单一运营者拥有全部 broker 子账户"下都无对象，**一律丢弃**；
只保留其**决策规则**（何时动 / 动到哪 / 动多少）。

| 臂 | 出处 | 触发 | 目标 | 幅度 | 代码 |
|---|---|---|---|---|---|
| `flexichain` | ACM TOPS 27(4) 2024 | 某片低于均分线 | 静态均分 ρ/n | 补足缺口 | `flexichain_plans` |
| `loopmax` | IACR ePrint 2022/1385 Alg.2 | 预测：ETTD < T_check+T_conf | 需求份额 + 安全边际 | **搬满（无死区）** | `loopmax_plans` |
| `spider` | USENIX NSDI 2020 §7.4 | 累计流水量到阈值 | 均分（equalize） | waterfilling 全量 | `spider_plans` |
| `pnps` | IEEE INFOCOM 2020 | 仅建链时一次 | λ 比例（一次性） | 一次性，此后冻结 | `pnps_target` |
| `autoloop` | IACR ePrint 2022/1385 | 余额穿 [0.3C, 0.7C] | band 中点 0.5C | 贪心配对 | `autoloop_plans` |
| `overpass_u` | IACR ePrint 2024/1526 | 低于目标一个死区 | **θ=1/n 均分** | QP 闭式解（无死区） | `overpass_target` |
| `overpass_d` | 同上 | 同上 | **θ∝λ 需求比例** | 同上 | `overpass_target` |
| `ucrb` | IACR ePrint 2025/1023 | **支付失败后**（反应式） | 无 | Σ移位 == 缺口 | `ucrb_plans` |

三处**必须写进论文的标定声明**（否则会被读成"苹果比橘子"）：

1. **绝对单位换算**。`spider` 原文的"每 10,000e 均衡一次"里 `e` 的分母是外部
   通道容量，与本平台的 wei 口径不可比，故按**相对自身资本**换算：
   阈值 = `sota_spider_vol_mult` × 该 broker 总资本 (n×C)，默认 0.25
   （≈ 每 broker 180 ETH 流水触发一次）。该倍数是可扫的旋钮，见下。
2. **时间尺度换算**。`loopmax` 的 `T_check = T_conf = 10 min`、安全边际 `2 min`
   按块比缩放：本平台块时间 1 s，故 horizon = `window+chi` 块（默认 30 块）、
   安全边际 3 块（= 2 min / 20 min × 30）。两者都由
   `sota_loopmax_horizon_blocks` / `sota_loopmax_safety_blocks` 显式给出。
3. **统一估计器**。所有臂共用同一个需求估计器（EWMA, hl=20，即 TDR 定稿臂的
   估计器）。这样各臂之间的差异可以**完全归因于决策规则**，而不是"谁的预测更准"。

## 3 怎么跑

```bash
export PATH="$HOME/.foundry/bin:$PATH"        # anvil 不在 PATH 时
cd <repo root>
ln -s ../exp_anvil_broker_qwen_local/trace trace   # 若 trace/ 缺失

# 先看效果（分析档，1 场 × 10 臂，产物标记 formal=false）
python3 experiments/exp033_sota_comparison/run.py --rounds 1

# 正式档（3 场 × 10 臂，40,000 CTX/臂）
python3 experiments/exp033_sota_comparison/run.py

# 打通链路用（1 broker × 80 CTX）
python3 experiments/exp033_sota_comparison/run.py --smoke --no-plot
```

产物在 `out/<时间戳>/`：
`raw_results.csv`（每臂每场一行）、`report.json`、`tasks/*/summary.json`（逐臂全量）、
`figs/exp033_pareto.png` + `SUMMARY_CN.md`（Pareto 图 + ρ* 表）。

runner 可断点续跑（`--resume <run目录>`）；改动源码或网格后续跑会被拒绝，
防止把两次不同代码的结果混在一起。

## 4 怎么读

- **ρ\* = 搬运次数 ÷ (plain relay − 该臂 relay)**，即"该方案所需的最小 κ/μ"。
  ρ\* 越小 = 对经济环境越宽容 = 越稳健。它**不依赖任何 gas/费率假设**，
  因此比"实测盈亏"更稳健——论文 §V-D 的 Eq.(11) 尚未把费用接进再平衡路径，
  本包如实沿用"经济阈值"而非"实测盈利"的表述。
- 图上**过 plain 点的射线簇** `y=(κ/μ)(x₀−x)`：线下方＝盈利区，上方＝亏损区。
  射线必须过 plain 点，因为"不再平衡"才是 broker 的默认选择。
- 叙事：所有对照臂要么落在**右侧**（relay 降不下来），要么落在**上方**
  （搬运爆炸）；TDR 应当在左下角。

## 5 已知限制

- 本包是**分析档/正式档的关系**：`--rounds 1` 的产物 `formal=false`，
  只能用于先看效果，**不得**作为论文数据引用；论文数据须跑满 3 场的正式档。
- `ucrb` 的反应输入是引擎的终审改判回报（`_submit_relay` 钩子），
  缺口 = 金额 − 该分片可用额；它没有前瞻，属"滞后型"对照。
- 未实现 `pickhardt`（公平份额需要通道对手方语义）与 `mcflow`
  （单运营者下退化为全量重排，已由 exp008 的 `proportional` 臂代表）。
- 单元测试只断言"我方按文献规则实现"（触发/目标/幅度三条），
  性能结论只认实测产物：`tests/test_sota_policies.py`。
