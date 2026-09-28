#!/usr/bin/env python3
"""exp016_sampled_direction: preparation is separate from chain execution."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from brokerlab.directional_experiment import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "sampled"))
