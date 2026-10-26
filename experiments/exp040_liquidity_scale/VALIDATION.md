> 历史v1验证记录：下面的340次配对方案已由用户最新要求替换，不再使用。当前为实验40仅TDR-on的170次扫描、实验41仅TDR-off的10次重复；请参照各自README及VALIDATION_v2.md。历史数据保留，不续跑旧manifest。

# 代码交付验证记录（2026-10-15）

正式340次运行尚未启动。下列结果是代码/管线验证，不是论文正式结果。

1. `python -m pytest experiments/exp040_liquidity_scale/test_design.py -q`：16项通过。覆盖340任务/2040万CTX矩阵、共享基准去重、配对种子、任务有效参数、嵌套前缀/热点节奏、固定费用与relay上限、坏数据/守恒失败/搬运不匹配拒绝。
2. 正式 `--dry-run`：340任务、20,400,000条用户CTX；两独立维度分别200与160次，共同基准20次去重。
3. `--prepare-only`：从真实trace选出200,000笔唯一CTX，400k候选不足后扩大到800k；得到304,234笔符合初始组约束的候选，选择200k；金额合计约558,404ETH，未对本金缩放。全部8个规模前缀保存哈希。
4. 真实Anvil SMOKE：2分片、2broker、共6次、320笔CTX全部完成；无失败，费用与资金守恒门全部通过。有TDR的两组分别完成2和4次真实burn/mint搬运，验证了非空搬款台账与成本汇总。
5. SMOKE首轮用 `--max-tasks 2` 停在2/6，再用同目录 `--resume` 完成其余4次；完成后重复续跑未增加attempt、未重执行完成任务。该测试发生在绘图纵轴文字缩短之前；随后仅修改绘图标签，并对同一原始数据重新绘图，未改执行或记账代码。新图已查看，坐标文字完整。
6. 新记账代码交叉核对历史exp030正式40k Plain/TDR两次：总潜在收入均84,000,000,000wei，实际收入分别20,225,100,000和84,000,000,000wei；TDR组3,118次搬运与台账一致。见output/figure_revision_audit_20261015/exp040_historical_accounting_crosscheck.json。
7. 当前最终代码另建正式准备目录 `out/20261015_211659_467108`；所有340任务仍pending。对它执行 `--prepare-only --resume` 通过；尝试修改q_min为.4续跑时如预期被配置签名拒绝，没有起链。

SMOKE数据与PNG/PDF预览：`out/20261015_210940_691976`。图明确标SMOKE、每点n=1，不能用于论文统计结论。

当前最终代码的200k母工作流已准备好，可直接从C盘项目根执行：

```powershell
python experiments/exp040_liquidity_scale/run.py --resume experiments/exp040_liquidity_scale/out/20261015_211659_467108
```

或先执行该正式计划的前两个任务作为全规模资源预检：

```powershell
python experiments/exp040_liquidity_scale/run.py --resume experiments/exp040_liquidity_scale/out/20261015_211659_467108 --max-tasks 2 --no-plot
```

如修改配置、源码、trace或后端路径，请新建运行；不要手工改签名绕过校验。更早的 `out/20261015_210858_694919` 仅用于初期选流检查，其配置早于最终归一化，不作为推荐续跑入口。
