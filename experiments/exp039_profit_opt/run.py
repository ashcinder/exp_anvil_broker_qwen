"""exp039 · TDR 经济目标参数优化（见仓库根 PLAN_profit_optimization_CN.md）。

本包承载计划里的各阶段任务；用 --config 选择阶段配置，run 目录按 <cell>/<stage> 分。
一个工作单元 = 一个 (balances_eth × ctx_per_broker) 格子，彼此独立、可多人并行。

Phase 0（迁移性检验）：把 40k 已知最优点 topup@45@@0.99@@20@0.7 与定稿参数
topup@45@@0.95@@20@0.3 成对放到 100k / 200k 上跑，各配 plain 基线。
判据：若最优点在两个规模下都优于定稿参数 ⇒ 参数序可迁移 ⇒ 只需在单格做完整搜索。

用法：
  python3 experiments/exp039_profit_opt/run.py --dry-run --config config.p0_100k.yaml
  python3 experiments/exp039_profit_opt/run.py \
      --config experiments/exp039_profit_opt/config.p0_100k.yaml \
      --run-dir experiments/exp039_profit_opt/out/c100k/p0 --no-plot

⚠ 同一台机器上不要并行两个单元：实测双路会把 relay 抬高且两个 worker 的 CPU 分配
不均（4.9 vs 6.4 线程），会系统性偏袒某一批臂。跨机器并行时各机须用不同
chain.base_port（config 里已写死各单元的端口）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp035_econ_retune.runner import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "exp039"))
