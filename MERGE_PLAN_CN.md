# 两仓合并对比与方案（local → GitHub 目标仓）

日期：2026-10-09。
- 目标仓：`exp_anvil_broker_qwen`（co-author GitHub 版，自有 git，HEAD `8cdf2b7`，3.1 MB）。
- 来源仓：`exp_anvil_broker_qwen_local`（你的本地版，41 GB，数据为主）。

## 1 总体格局

| 维度 | GitHub 目标仓 | local 来源仓 |
|---|---|---|
| 实验包 | **29 个**（exp001–exp029） | 11 个（exp001–008 + exp009/010/011 自有名） |
| brokerlab 模块 | 20 个（含 6 个独有：directional×3、mapped_workload、reporting、two_shard_factorial） | 14 个（无独有） |
| 测试 | **23 个** | 11 个 |
| 顶层文档 | 多 4 份学生/Docker 文档 + 生成脚本 | 多 4 份：METHOD_final、REGIME_SHIFT、BUGS_20261009、Bug PDF |
| 数据 | 无（.gitignore 排除 trace/、out*/、figs/ 等） | trace 12.3 GB、exp011 数据 16 GB、图源 9.5 GB |

两边同源，自 2026-09 中旬起分叉：
GitHub 线走向"大规模实验矩阵 + 学生文档 + Docker"；
local 线走向"编排器网格（exp009/010/011）+ 图表源数据 + 文档"。

## 2 代码层：9 个共有 brokerlab 文件，方向全部一致

| 文件 | GitHub 独有行 | local 独有行 | 判定 |
|---|---:|---:|---|
| broker_engine.py | 21 | **0** | GitHub 严格超集 |
| chain.py | — | — | SO_REUSEADDR 两边都有；GitHub 更多（127 行差异为新增注释与特性） |
| config.py | 5 | 2（`int` 默认值等旧写法） | GitHub 胜（其 README 已记"改 float 修复"） |
| real_data.py | 25 | 6 | GitHub 胜：合约过滤重构为 `_flag_is_true/_is_contract_creation` + 可配置 |
| procmon.py | 7 | 9（注释文案） | GitHub 胜（多 `cpu_s()`） |
| tdr_policy.py | 多 | 24 | GitHub 胜：`EwmaDemand.effective_window_blocks()` 等；策略函数同构 |
| tdr_engine.py | 多 | 19 | GitHub 胜：local 独有的"lost_moves 带 burn_block"被其 richer snapshot 覆盖 |
| tdr_hook.py | 多 | 72 | GitHub 胜：envelope 多 `move_events` + `balance_snapshots`；local 的"搬移块号"记录被覆盖 |
| tx.py | 多 | 多 | **双方各自修了同一 bug**：GitHub 覆盖 `get_balance`+`sync_nonces` 且只捕 `Web3RPCError`（更严谨）⇒ GitHub 胜 |

**结论：共有代码全部取 GitHub，local 无需回灌任何一行。**

## 3 实验层

- **exp001–008**：两边都改过；GitHub 版是演进版（exp008 有 valve sweep + calibrate_balance；
  exp003 有 `frac` 参数并去掉了 fund 四舍五入）。取 GitHub。
- **local 自有 3 包**（exp009_full_matrix 56K / exp010_param_grid 1.6G / exp011_param_sensitivity 16G）
  与 GitHub 的 20 个包**名字前缀冲突**（exp009/010/011 已被占用）且功能重叠：
  GitHub 已有 exp019–024（EWMA/ε 搜索）、exp028（半衰期）、exp029（敏感性）、exp025/026（regime shift）。

## 4 测试层
GitHub 23 个 ⊇ local 11 个（local `test_tx.py` 被 GitHub `test_tx_retry.py` 覆盖）。取 GitHub。

## 5 文档层
- local 独有：`METHOD_final_CN.md`、`REGIME_SHIFT_CN.md`、`BUGS_20261009_CN.md`、`Experiments 7 Bug.pdf`。
- GitHub 独有：`DOCKER_SSH.md`、`EXPERIMENTS_09_18_DESIGN_CN.md`、`PROJECT_*.md`、`generate_guide_pdf.py`。
- 共有文档（README/REPORT/DESIGN/PLAN/AUDIT/GUIDE）：两边都改过，GitHub 版为主。

## 6 submit_regime_shift：唯一真正双向的目录

| 项 | GitHub | local |
|---|---|---|
| config.yaml | ✅ | ❌ |
| data/、results/ | ❌ | ✅ |
| reports/（3 份） | ❌ | ✅ |
| scripts/ | 缺 pack_for_coauthor.py | 多 pack_for_coauthor.py |
| final_table.py / plot_final.py | 有（版本旧） | 有（版本新） |

## 7 建议的合并动作（机械部分，已可执行）

1. **以 GitHub 仓为基线**（不动其任何既有文件的内容）。
2. 从 local 复制：4 份独有文档（含 PDF）。
3. submit_regime_shift：以 local 的 scripts/reports 覆盖同名文件 + 补入 GitHub 的 config.yaml。
4. 数据（trace、图源、exp 产物）：.gitignore 已排除，**不随 git**；是否物理复制到目标目录待定。
5. 合并后验证：目标仓 `pytest tests/ -q` + `python -m brokerlab doctor`。

## 8 待你拍板的 4 个决策点

- **A. local 三个自有实验包**：搬入（需改名，如 exp030+）／弃用（GitHub 已有等价搜索）？
- **B. submit_regime_shift**：双向合并（local 脚本+报告 + GitHub config）？
- **C. 数据目录**：trace（12 GB）与图源（9.5 GB）是否复制进目标目录？
- **D. local 的实验产物 out/**（如 exp010 网格 1.6 GB）是否复制？

---

## 9 执行记录（2026-10-09，已完成）

**决策**（用户 4 问）：三个实验包全部搬入并改名；submit_regime_shift 双向合并；
数据不复制；实验产物 out/ 不搬。

**已执行**：
1. 文档合入目标仓（6 份）：
   `EXPERIMENT_REPORT_CN.md`、`EXPERIMENT_DESIGN_CN.md`（两者 local 为超集，覆盖）、
   `METHOD_final_CN.md`、`REGIME_SHIFT_CN.md`、`BUGS_20261009_CN.md`、`Experiments 7 Bug.pdf`。
2. 三个实验包改名搬入（只带代码与文档，不含 out/）：
   `exp009_full_matrix → exp030_full_matrix`、
   `exp010_param_grid → exp031_param_grid`、
   `exp011_param_sensitivity → exp032_param_sensitivity`。
   同步修正包内自我引用：config 的 label、README 路径、
   打印标签（`exp030 ·`/`exp031 ·`/`exp032 ·`）、配置键名
   （`exp011_sessions/exp011_relay_median_max → exp032_*`）。
3. submit_regime_shift 双向合并：取 local 的 `scripts/final_table.py`、`plot_final.py`、
   `pack_for_coauthor.py` 与 `reports/` 三份；保留 GitHub 的 `config.yaml`、`run.py`、
   `probe_workload.py`；README 以 local 为基，并入 GitHub 的"交付物目录"与"运行注意"两节。
4. 根 `README.md`：保留 GitHub 版为基，追加"参数网格与敏感性实验（exp030–exp032）"与
   "文档索引"两节。
5. 本文件拷入目标仓。

**按决策未做**：trace/ 与图源数据不复制；`out/` 产物不搬；
local 的 `tests/test_tx.py` 不并入（其功能被 GitHub 的 `tests/test_tx_retry.py` 覆盖）。

**代码层结论**：9 个共有 brokerlab 模块逐一比对，GitHub 版为超集
（tx.py 双方各自修了同一竞态 bug，GitHub 覆盖两个调用点且只捕 `Web3RPCError`），
**local 零行回灌**。

**验证**：
- 三个新包 `py_compile` 全过；exp030/exp031 `--dry-run` 正常；
  exp003 管线的 summary 字段（relay/transfers/moved/legs_share/gates/burn_reconcile）兼容。
- 目标仓 `pytest tests/ -q`：**315 passed / 5 failed**。
  5 个失败全部是 `FileNotFoundError: Clean trace not found`——
  `trace/ETH_cleaned.csv` 被 .gitignore 排除，裸克隆仓库同样失败，**与本次合并无关**。
  如需全绿：把 `ETH_cleaned.csv`（314 MB）放到目标仓 `trace/` 下即可。

**未提交**：目标仓现有 6 个修改文件 + 新增包/文档处于未提交状态，
是否 commit 由用户决定（仓库为该项目的 git 主线）。
