"""exp034: clean trace, rotating hotspots, independent alpha recipient threshold and OAT sensitivity."""
import os
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from experiments.exp025_regime_shift_sensitivity.runner import main
if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main(HERE, "exp034"))
