"""Experiment 33: SOTA 再平衡方案对照（复用 exp025 的可恢复串行 runner）。

臂的决策规则在 brokerlab/sota_policies.py；负载/路由种子/资金与 exp025 逐参数
相同，所以本包的每个点都与已发表的 plain/TDR 点同负载可比。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp025_regime_shift_sensitivity.runner import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "exp033"))
