# 突变注入实验 · 交付包

**一句话**：在真实 trace 上注入可控的需求突变，做出 TDR 参数的敏感曲线。
最终 **hl 与 q_min 都出现内点最优**（每点 n=3）。

## 先读哪一份

1. `reports/REGIME_SHIFT_CN.md` —— **本次改了什么、为什么、结果如何**。
   含数据集旋钮的逐项图解（这是最不好理解的部分）。
2. `reports/EXPERIMENT_REPORT_CN.md` §14.10 —— 最终结果表。
   §14.1–§14.9 是过程记录，口径已被 §14.10 取代。
3. `results/final_smile.png` —— 两条 U 曲线。

## 目录

| 目录 | 内容 |
|---|---|
| `reports/` | 全部中文报告（6 份）|
| `results/runs_tidy.csv` | **一行一跑**的汇总，箱线图直接可用 |
| `results/final_config_table.md` | 六张汇总表（含 TDR 有效性对照）|
| `results/final_smile.png` | 两条 U 曲线（中位 + 极值带）|
| `data/summaries/` | 每跑的 summary.json 原件（48 个，含全部指标）|
| `data/tdr_moves/` | TDR 转账台账，gzip（含 burn/mint 块号）|
| `scripts/` | 复现脚本 |
| `config.yaml` | 从已保存的运行参数恢复的单次实验配置（hl=20、q_min=0.3）|

## 箱线图数据

`results/runs_tidy.csv` 每行一跑，列见文件头。已按 `sweep` 分组：

- `sweep=hl`：x ∈ {2,4,10,20,50,150,400}，q_min 固定 0.3
- `sweep=q_min`：x ∈ {0,0.1,0.3,0.5,0.7,0.9}，hl 固定 10
- `sweep=baseline`：x ∈ {plain,valve,proportional}

已按参数值聚合的五数概括（min / Q1 / 中位 / Q3 / max）：

**hl**（relay）

| hl | n | min | Q1 | 中位 | Q3 | max |
|---|---|---|---|---|---|---|
| 2 | 3 | 232 | 232 | 307.0 | 375 | 375 |
| 4 | 3 | 90 | 90 | 91.0 | 136 | 136 |
| 10 | 3 | 139 | 139 | 183.0 | 198 | 198 |
| 20 | 3 | 91 | 91 | 103.0 | 111 | 111 |
| 50 | 3 | 107 | 107 | 118.0 | 130 | 130 |
| 150 | 3 | 95 | 95 | 95.0 | 233 | 233 |
| 400 | 3 | 117 | 117 | 178.0 | 182 | 182 |

**q_min**（relay）

| q_min | n | min | Q1 | 中位 | Q3 | max |
|---|---|---|---|---|---|---|
| 0 | 3 | 245 | 245 | 316.0 | 365 | 365 |
| 0.1 | 3 | 127 | 127 | 153.0 | 235 | 235 |
| 0.3 | 3 | 81 | 81 | 121.0 | 193 | 193 |
| 0.5 | 3 | 197 | 197 | 204.0 | 335 | 335 |
| 0.7 | 3 | 6917 | 6917 | 8584.0 | 8684 | 8684 |
| 0.9 | 3 | 26428 | 26428 | 26585.0 | 26954 | 26954 |

## 复现

在**仓库根目录**运行，参数已写入本包 `config.yaml`：

```powershell
python submit_regime_shift/scripts/run.py --config submit_regime_shift/config.yaml --out-root submit_regime_shift/scripts/out/arm_Y_hl20_r1
```

默认只运行一次 `hl=20、q_min=0.3`。修改 YAML 的 `exp.arms`，或用
`--set "exp.arms=topup@45@@0.95@@4@0.3"`，即可改为 `hl=4`。
q_min 扫描时固定 hl=10，例如 `--set "exp.arms=topup@45@@0.95@@10@0.1"`。
每个设置需另行运行三次，输出目录用 `_r1`、`_r2`、`_r3` 区分。

配置取自 `data/summaries/arm_Y_hl20_r1.json` 的参数快照，保留其运行参数，
删除旧批次网格及运行后统计，改用仓库相对 trace 路径。
另显式设置 `shift_to_shard=11`：当前提交脚本在该值缺省为 -1 时直接关闭注入，
即使设置了 `shift_cycle` 也不生效。历史快照没有记录该开关，因此不能据此保证
当前脚本与历史运行完全一致；本配置用于执行报告所描述的热点轮换设计。
当前仓库的 `brokerlab` 也可能已更新，重新运行的数值可能与已交付结果不同。

**必须串行**：并行会让 TDR 结果漂移（报告 §13.2）。

## 未包含

- **逐笔明细 `ctx_rows.csv`**（每跑 4.7 MB，含 4 万笔的逐笔延迟/跳数/路由）。
  需要可与 co-author 另行传递。
- **anvil 逐分片日志**（每跑约 20 MB，仅排障用）。

## 复现环境

Python + `brokerlab` 包（在本仓库内）+ Foundry 的 anvil。
`python -m brokerlab doctor --config configs/smoke.yaml` 可体检。
