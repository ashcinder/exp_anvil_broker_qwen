"""exp035 · 经济目标下的 TDR 参数重调。

用法：
  # 分析档（1 场，10 个臂：plain + eps{0.8,0.95,0.99}×q_min{0.1,0.3,0.7}，约 2.7 小时）
  python3 experiments/exp035_econ_retune/run.py --rounds 1 \
      --run-dir experiments/exp035_econ_retune/out/grid

  # 只看计划、不起链
  python3 experiments/exp035_econ_retune/run.py --dry-run

  # 正式档（3 场）去掉 --rounds 1。改网格改 config.yaml 的 exp.grid。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp035_econ_retune.runner import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "exp035"))
