"""Experiment 25: q_min, EWMA half-life and epsilon, 40000 CTX x 10 rounds."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp025_regime_shift_sensitivity.runner import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "exp025"))
