# exp035 · 经济目标下的 TDR 参数重调

## 1 为什么有这个包

exp034 把 TDR 的费用账算清后，同一批数据里出现了一个刺眼的对比：

| 变体（轮转热点 + 45 ETH/片 + 4 万笔） | relay | 搬运 y | ρ\* | 净收益 |
|---|---:|---:|---:|---:|
| **plain（不搬运）** | 30 329 | 0 | — | **0** |
| **topup（定稿参数 eps=0.95, q_min=0.3）** | 14 | 3 187 | **0.105** | **−156 F** |

即：**在 45 ETH 下，定稿的 TDR 参数每 4 万笔净亏 156 F，还不如什么都不做。**

而历史上从没有人用这个口径选过参数。老调优的主指标是：

- **exp008**：relay 优先，搬运次数**只在 relay 打平时**才当裁判（`PARAMETERS_round2_CN.md` 第 6 条，字典序）；
- **exp028 / exp029**：`C = Relay CTX + 完成搬运次数`，**等权相加**，并自注「C 是等权次数，
  不是 gas/字节/时延成本；如需成本权重，另行预先指定」。

费用模型（与 exp034 同一套账）：

$$\text{净收益} = F\cdot\big[\beta\cdot\Delta n_b - y\big],\qquad \rho^\* = \frac{y}{\Delta n_b}$$

少一笔 relay 只值 $\beta F$（broker 少拿的抽成），一笔搬运实付 $1F$ —— **两者不等权**，
权重是 β 不是 1。等权的 C 相当于把 β 当成 1，会过度再平衡 10 倍。
更关键的是：**relay 型目标只在 TDR 家族内部选，从不问「要不要做 TDR」**。

## 2 这个包做什么

同一批网格点，**同时**按四种目标排名，看最优解会不会分岔：

| 目标 | 含义 |
|---|---|
| `relay` 最小 | 老 exp008 的字面目标 |
| `C = relay + y` 最小 | exp028/029 的等权主指标 |
| `ρ*` 最小 | 该方案不亏所需的最低抽成 |
| **净收益最大** | **β·Δn_b − y，且网格里含 plain（"不搬运"）这个选项** |

**体制与 exp025/exp033 发布设定逐参数相同**（轮转 11/3、45 ETH/片、4 万笔、route_seed 7、hl=20），
**不动资金**，所以结论与已有 45 ETH 结果直接可比、不使其作废。

## 3 网格

```
epsilon ∈ {0.8, 0.95, 0.99}   # 越大触发越懒（可用余额 < (1-eps)·target 才动）
q_min   ∈ {0.1, 0.3, 0.7}     # EWMA 目标的持仓地板比例
half_life = 20                # 冻结在定稿值
include_plain = true          # 经济目标的"不参与"选项
```

9 个 Top-up 点 + 1 个 plain 基线 = 10 个臂 × 4 万 CTX。

- `eps=0.95, q_min=0.3` 是**定稿值**（eps 由 exp019–024 在 **150 ETH** 选出，恰为当时网格右端点；
  q_min=0.3 由 exp025/exp028 在 45 ETH 定）；
- `q_min=0.1` 对应 exp008 锚点臂的取值；
- `eps=0.99` 探"接近关闭"；`eps=0.8` 探"更激进"，用来确认曲线在激进一侧确实变差。

## 4 怎么跑

```bash
# 分析档（1 场 × 10 臂，约 2.7 小时）
python3 experiments/exp035_econ_retune/run.py --rounds 1 \
    --run-dir experiments/exp035_econ_retune/out/grid

# 只看计划
python3 experiments/exp035_econ_retune/run.py --dry-run

# 正式档（3 场）去掉 --rounds 1
```

改网格改 `config.yaml` 的 `exp.grid`，不用改代码。
无人值守用 `experiments/run_until_done.sh`（自动 `--resume`）。

## 5 产物

`out/grid/` 下：`raw_results.csv`、`report.json`、`econ_grid.csv`（含四种排名的名次列）、
`SUMMARY_CN.md`（四种目标各自的最优解 + 结论）、`figs/econ_grid.png`
（左：ρ\*(eps) 与 β 盈亏线；右：净收益(eps) 与 plain=0 线）。

收尾出表出图由本目录的 `analyze.py` 完成，也可以对已完成的点单独跑：

```bash
python3 experiments/exp035_econ_retune/analyze.py --source experiments/exp035_econ_retune/out/grid
```

## 6 实现说明（与 exp025 的差异）

`runner.py` 复用 exp025 的 `pipeline` / `paper_data` / `brokerlab`，**不改共享源码、不 vendor**，
所以已有 exp025/033/034 结果的源码指纹不受影响。三处差异：

1. 任务来自 `exp.grid`（epsilon × q_min）+ plain，而不是 `exp.arms`；
2. 任务 id 与 summary 文件名带参数 —— exp025 用 `name.split("@")[0]` 当 value，
   同一 policy 的多个网格点会撞成同一个 id 和同一个 `summary_*.json`，静默丢数据；
3. 收尾调用本目录的 `analyze.py`（四种目标评分），而不是 paper 图脚本。

## 7 读法

- 若 **净收益最大 = plain** ⇒ 45 ETH 下没有任何 TDR 参数值得做，ρ\* 全在 β 之上。
  论文的经济结论必须绑定资金水平（对照 exp034-B：150 ETH 下 ρ\*=0.058，稳赚）。
- 若 **某个点净收益 > 0 且 ≠ 等权 C 选出的点** ⇒ 老调优的目标函数确实选错了点。
- 若 **四种目标选出同一个点** ⇒ 目标函数之争在这批数据上不改变结论，可以放心用老口径。
- 若 **净收益随 eps 单调上升直到 0.99 还在涨** ⇒ 最优点在网格外（甚至就是"关闭"），
  与 `eps=0.95` 落在 exp023/024 网格右端点是同一个征兆。
