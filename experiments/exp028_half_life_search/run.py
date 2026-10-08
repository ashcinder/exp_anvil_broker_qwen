"""Finite-horizon half-life search. Run real Anvil trials only when requested."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from experiments.exp028_half_life_search.runner import main

if __name__ == '__main__':
    raise SystemExit(main(HERE))
