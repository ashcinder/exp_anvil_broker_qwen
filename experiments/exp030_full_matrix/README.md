# exp009 · exp001–008 全功能开关矩阵（编排器）

**这是什么**：一个总入口。config.yaml 里每个 stage 一个开关，
按依赖序把各实验包的 run.py 逐个子进程调用，收退出码与产物，
聚合成一份 matrix_report.json。**不改其它任何代码**。

**它不是什么（诚实边界）**：
- 它不合并管线。各包的机制、判据、参数都在自己包里，exp009 只调用。
- 因此它不能"跨包组合"：比如给动态管线打开 B2E 费用——
  费用代码只在 exp004 的阻塞路径里，动态引擎没有这个功能，
  开关解决不了未实现的特性。真要合并是另一个工程量。
- exp005 线程方案判负返回 1 属预期结论，用 expected_rc 白名单表达。

**stage 开关表**（enabled 即开关；overrides 转发为 --set）：

| stage | 覆盖能力 | 来自哪条证据链 |
|---|---|---|
| exp001 | 机制双路径开销 | 论文机制表（延迟/通信/足迹） |
| exp002 | 无 TDR 耗尽基线 | 手稿 C1-C4 复现 |
| exp004 | B2E 恒等式 | 费用账 |
| exp005 | 三容器基底 | 进程基底的裁定 |
| exp006 | 速率不变性+上限 | 逻辑时钟根治（审计 F1） |
| exp007 | 动态路由+终审 | M3 验收 |
| exp003 | TDR 双臂+全部策略 token | M4 验收与策略族 |
| exp008 | 定稿方案多场生产+聚合 | 论文数据生产 |

**profile**：
- `smoke`：每 stage 追加缩规模覆盖（只压 N/档数，不动判据逻辑）。
  全矩阵 smoke ≈ 30-40 分钟。
- `full`：只用 overrides。全矩阵 full ≈ 4-5 小时（含 exp008 两场）。
- 单 stage 冒烟：`python run.py --only exp001`。

**运行**：
```bash
python run.py --dry-run                  # 看执行计划
python run.py --only exp005,exp001       # 跑指定 stage
python run.py                            # 全开关（按 profile）
python run.py --set exp.profile=full     # 正式全矩阵（后台，数小时）
```

**产物**：`out/<ts>/matrix_report.json`（每 stage 的 rc/期望/用时/产物路径/
判据摘要）+ `<stage>.log`（各包完整 stdout/stderr）。

**判据**：全部 stage 的 rc 落在各自 expected_rc 内 ⇒ 退出 0。
matrix_report 只摘要 gates/hypotheses/verdicts 与 relay 数，
细数据看各包自己的 summary.json（路径在报告里）。

**依赖顺序即开关表顺序**（编排器不自动推依赖）：
1→2→4→5→6→7→3→8，与证据链一致：机制→耗尽→费用→基底→速率→路由→TDR→生产。

**当前状态**：dry-run 与 exp001 单 stage 真跑已验证（见下）。

**最近结果**：（首次全矩阵 smoke 后回填）
