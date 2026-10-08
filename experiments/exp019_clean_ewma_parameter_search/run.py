#!/usr/bin/env python3
"""Run 10-replicate clean-data finite-window EWMA OAT searches via exp010."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = HERE.parent / "exp010_tdr_parameter_search" / "run.py"


def main():
    cmd = [
        sys.executable, str(BASE),
        "--config", str(HERE / "config.yaml"),
        "--space", str(HERE / "search_space.yaml"),
        "--profile", "search",
        "--stage", "oat",
        "--only", "ewma_window_blocks,epsilon,ewma_half_life",
        "--output-root", str(HERE / "out"),
        *sys.argv[1:],
    ]
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
