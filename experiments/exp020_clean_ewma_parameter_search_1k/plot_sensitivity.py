#!/usr/bin/env python3
"""Render exp020's sensitivity figures from completed result files only."""
import argparse
from pathlib import Path

from experiments.exp019_clean_ewma_parameter_search.plot_sensitivity import draw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True,
                        help="exp020 output directory containing manifest.json")
    args = parser.parse_args()
    draw(args.run.resolve())


if __name__ == "__main__":
    main()
