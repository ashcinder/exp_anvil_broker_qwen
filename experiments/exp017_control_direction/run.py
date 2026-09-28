#!/usr/bin/env python3
"""Synthetic control-bit direction experiment; defaults to full execution."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from brokerlab.directional_experiment import main

if __name__ == "__main__":
    raise SystemExit(main(HERE, "controlled"))
