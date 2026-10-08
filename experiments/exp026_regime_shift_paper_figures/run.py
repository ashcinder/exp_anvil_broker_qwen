"""Experiment 26: measured data for the revised Fig1, Fig5a, Fig5c and Fig5d."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp025_regime_shift_sensitivity.runner import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "exp026"))
