"""Validate the actual clean workload and OAT task parameters without starting Anvil."""
import argparse
import json
import sys
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brokerlab.config import load_config, apply_overrides
from brokerlab.tdr_engine import TdrAgent
from experiments.exp025_regime_shift_sensitivity.pipeline import build_workload, _arm_spec
from experiments.exp025_regime_shift_sensitivity.runner import digest, tasks_for, write_json

def main():
    here = Path(__file__).parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=here/'config.yaml')
    p.add_argument('--space', type=Path, default=here/'search_space.yaml')
    p.add_argument('--set', action='append', default=[])
    p.add_argument('--output', type=Path, default=here/'out_preflight_v3')
    a = p.parse_args()
    cfg = apply_overrides(load_config(a.config), a.set)
    space = yaml.safe_load(a.space.read_text(encoding='utf-8'))
    tasks = tasks_for(cfg, 'exp034', space)
    for task in tasks:
        _, engine, _, _, eps, _, hl, q, _ = _arm_spec(task['token'])
        TdrAgent(cfg.chain.num_shards, policy=engine, epsilon=eps, q_min=q,
                 demand_ewma_half_life=hl, low_water_ratio=task["low_water_ratio"])
    a.output.mkdir(parents=True, exist_ok=True)
    workload, _ = build_workload(cfg, a.output)
    result = dict(revision='exp034-independent-lowwater-v3', evaluation_count=len(workload),
                  workload_sha256=digest(workload), tasks=len(tasks),
                  total_evaluation_ctx=len(tasks)*len(workload), search_space=space,
                  repetitions=cfg.exp['repetitions'], chain_started=False)
    write_json(a.output/'preflight.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
