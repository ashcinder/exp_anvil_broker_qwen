#!/usr/bin/env python3
"""Run exp008's existing pipeline with an isolated clean-trace configuration.

--check validates setup without starting nodes, producing outputs, or reading CSV
contents. The exp008 and exp003 implementations are reused, not copied or edited.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.chain import resolve_anvil_bin
from brokerlab.config import apply_overrides, load_config


def load_exp008():
    source = HERE.parent / "exp008_tdr_schedule_final" / "run.py"
    spec = importlib.util.spec_from_file_location("exp011_exp008_runner", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # main() uses HERE for its default config and output location. ROOT and the
    # existing EXP003_RUN retain their original, correct project-level paths.
    module.HERE = HERE
    return module


def check_plan(cfg, runner):
    trace = Path(cfg.traffic.real_csv_path or "")
    if not trace.is_file():
        raise FileNotFoundError(f"Trace file not found: {trace}")
    tags = runner._expected_arm_tags(cfg)
    if not tags or len(tags) != len(set(tags)):
        raise ValueError("Arms must be nonempty and have unique output tags")
    if cfg.exp["exp008_final_arm"] not in tags:
        raise ValueError("exp008_final_arm does not match an actual arm tag")
    sessions = int(cfg.exp["exp008_sessions"])
    count = cfg.scale.num_brokers * int(cfg.exp["ctx_per_broker"])
    if sessions < 1 or count < 1:
        raise ValueError("Session and CTX counts must be positive")
    if float(cfg.exp["coordinator_mint_budget_eth_per_shard"]) <= 0:
        raise ValueError("Coordinator mint budget must be positive")
    return {
        "experiment": cfg.label,
        "config_default": str(HERE / "config.yaml"),
        "trace": str(trace.resolve()),
        "anvil": resolve_anvil_bin(cfg.chain.anvil_bin),
        "sessions": sessions,
        "ctx_per_arm": count,
        "arm_runs": sessions * len(tags),
        "target_rate": cfg.exp["rate"],
        "route_seeds": [int(cfg.exp["route_seed"]) + 10 * k for k in range(sessions)],
        "arms": tags,
        "final_arm": cfg.exp["exp008_final_arm"],
        "output_root": str(HERE / "out"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(HERE / "config.yaml"))
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    parser.add_argument("--check", action="store_true", help="Check setup only; no chain experiment")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    runner = load_exp008()
    plan = check_plan(cfg, runner)
    print("exp011: " + json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.check:
        print("CHECK OK: no nodes started, no experiment outputs created.")
        return 0
    # No --check is present here. exp008 parses the same --config/--set options.
    # Child processes execute exp003's real path, preserving Windows spawn.
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
