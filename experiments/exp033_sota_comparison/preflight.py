"""Validate actual workload and PnP planning without starting Anvil."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brokerlab.config import load_config, apply_overrides
from brokerlab.sota_native import pnps_plan, REVISION
from experiments.exp025_regime_shift_sensitivity.pipeline import build_workload, _arm_spec
from experiments.exp025_regime_shift_sensitivity.runner import digest, tasks_for, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.yaml'))
    parser.add_argument('--set', action='append', default=[])
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'out_preflight')
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), args.set)
    args.output.mkdir(parents=True, exist_ok=True)
    workload, _ = build_workload(cfg, args.output)
    plans = {}
    for token in cfg.exp['arms'].split(','):
        name, engine, fund, *_ = _arm_spec(token)
        if engine == 'pnps':
            capital = int((fund if fund is not None else cfg.exp['balances_eth']) * 10**18) * cfg.chain.num_shards
            plans[name] = pnps_plan(cfg.exp.get('sota_training_samples', []), capital,
                                    cfg.exp.get('sota_pnps_outage', .05))
    result = dict(revision=REVISION, evaluation_count=len(workload),
                  workload_sha256=digest(workload),
                  training_sha256=hashlib.sha256((args.output/'training.json').read_bytes()).hexdigest(),
                  tasks=len(tasks_for(cfg, 'exp033')), pnps_plans=plans,
                  chain_started=False)
    write_json(args.output/'preflight.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
