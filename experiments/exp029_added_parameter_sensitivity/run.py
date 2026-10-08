"""Run added sensitivity points, ten rounds by default; supports epsilon=1 only."""
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from experiments.exp029_added_parameter_sensitivity.runner import main

if __name__=='__main__':raise SystemExit(main(HERE))
