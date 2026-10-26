"""Entry point for exp034 in the repaired exp033 project."""
import os
import runpy
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2] / "output" / "exp033_main_audit"
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
if __name__ == "__main__":
    runpy.run_path(str(ROOT / "experiments" / "exp034_qmin_floor" / "plot_pareto.py"), run_name="__main__")
