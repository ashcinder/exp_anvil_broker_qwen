"""Serial paired-seed search using exp025's current validated execution pipeline."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from brokerlab.config import apply_overrides, load_config, to_params_dict
from brokerlab.procmon import RSSWatchdog
from experiments.exp025_regime_shift_sensitivity import runner as base
from experiments.exp025_regime_shift_sensitivity import pipeline, paper_data

ROOT = Path(__file__).resolve().parents[2]
GRIDS = {
    'coarse': [2, 4, 8, 10, 20, 50, 100, 200, 400, 800, 1000],
    'extended': [2, 4, 8, 10, 20, 50, 100, 150, 200, 250, 300, 350,
                 400, 450, 500, 600, 700, 800, 1000],
}

def parse_values(text):
    try:
        values = [float(v.strip()) for v in text.split(',')]
    except ValueError as e:
        raise ValueError('--values must contain comma-separated numbers') from e
    if not values or any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Half-lives must be positive finite values')
    if len(set(values)) != len(values):
        raise ValueError('Duplicate half-life values')
    return sorted(int(v) if v.is_integer() else v for v in values)

def source_fingerprint():
    files = [Path(__file__), Path(__file__).with_name('run.py'),
             Path(base.__file__), Path(pipeline.__file__), Path(paper_data.__file__)]
    files += sorted((ROOT / 'brokerlab').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

def make_tasks(cfg, values, order_seed):
    space = {'half_life': {'values': values, 'fixed': {'q_min': .3, 'epsilon': .95}}}
    tasks = base.tasks_for(cfg, 'exp025', space)
    reordered = []
    for rep in range(1, int(cfg.exp['repetitions']) + 1):
        group = [t for t in tasks if t['round'] == rep]
        random.Random(order_seed + rep).shuffle(group)
        reordered.extend(group)
    return reordered, space

def main(here):
    parser = argparse.ArgumentParser(description='exp028: half-life search, serial paired-seed runs')
    parser.add_argument('--stage', choices=['coarse', 'fine', 'extended'], default='coarse')
    parser.add_argument('--values', help='Custom half-lives, comma separated; required for fine')
    parser.add_argument('--rounds', type=int, default=1, help='Repetitions per point; default 1 for a preliminary trend')
    parser.add_argument('--route-seed', type=int, help='Defaults: coarse/extended=7; fine=1007')
    parser.add_argument('--order-seed', type=int, default=2842)
    parser.add_argument('--config', type=Path, default=here / 'config.yaml')
    parser.add_argument('--set', action='append', default=[])
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--smoke', action='store_true', help='80 CTX, one broker, one round, one point; no scientific inference')
    parser.add_argument('--no-plot', action='store_true', help='Skip final analysis/plot; data are still saved')
    args = parser.parse_args()
    if args.stage == 'fine' and not args.values:
        parser.error('fine requires --values selected after examining coarse results')
    values = parse_values(args.values) if args.values else GRIDS[args.stage]
    rounds = args.rounds
    if rounds < 1:
        parser.error('At least one round required')
    if len(values) < 3 and not args.smoke:
        parser.error('At least three half-life points required to assess a curve')
    seed = args.route_seed if args.route_seed is not None else (1007 if args.stage == 'fine' else 7)
    cfg = apply_overrides(load_config(args.config), args.set)
    # q_min and epsilon are intentionally locked to isolate half-life.
    cfg = apply_overrides(cfg, [f'exp.repetitions={rounds}', f'exp.route_seed={seed}',
                               'exp.route_seed_step=10', 'exp.tdr_q_min=0.3',
                               'exp.tdr_epsilon=0.95', 'exp.tdr_ewma_window_blocks=0',
                               'exp.tdr_ewma_two_sided=false'])
    if args.smoke:
        values = [values[0]]
        cfg = replace(cfg, scale=replace(cfg.scale, num_brokers=1),
                      broker=replace(cfg.broker, initial_balance_eth=5))
        cfg = apply_overrides(cfg, ['exp.repetitions=1', 'exp.ctx_per_broker=80',
                                   'exp.balances_eth=5', 'exp.rate=5', 'exp.drain_tail_s=240'])
    expected = cfg.scale.num_brokers * int(cfg.exp['ctx_per_broker'])
    if not args.smoke and expected != 40000:
        parser.error('This design fixes 40,000 CTX/run; longer-horizon trials need a separate design')
    tasks, space = make_tasks(cfg, values, args.order_seed)
    signature = base.digest(dict(params=to_params_dict(cfg), space=space,
                                 stage=args.stage, order_seed=args.order_seed, smoke=args.smoke))
    print(f'exp028 {args.stage}: {len(tasks)} runs x {expected:,} CTX; serial; smoke={args.smoke}', flush=True)
    if args.dry_run:
        print(json.dumps(dict(stage=args.stage, values=values, rounds=cfg.exp['repetitions'],
                              route_seeds=[seed + 10 * r for r in range(int(cfg.exp['repetitions']))],
                              fixed={'q_min': .3, 'epsilon': .95}, total_runs=len(tasks),
                              total_user_ctx=len(tasks) * expected, first_tasks=tasks[:3],
                              output_root=str(args.output_root or here/'out'/args.stage),
                              serial=True, independent_fresh_chain_per_run=True), indent=2))
        return 0
    run = args.resume.resolve() if args.resume else (
        (args.output_root or here/'out'/args.stage).resolve() / datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    if args.resume:
        manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('experiment') != 'exp028' or manifest['signature'] != signature:
            raise ValueError('Resume requires the original stage, values, seeds, rounds and overrides')
        if manifest['source_files'] != source_fingerprint():
            raise ValueError('Execution source changed; do not pool results from different implementations')
    else:
        run.mkdir(parents=True)
        manifest = dict(experiment='exp028', stage=args.stage, smoke=args.smoke,
                        signature=signature, source_files=source_fingerprint(),
                        total_ctx_per_arm=expected, repetitions=cfg.exp['repetitions'],
                        sweep_space=space, tasks=tasks, order_seed=args.order_seed,
                        primary_metric='relayed + transfers_done', secondary_metric='relayed',
                        historical_data_pooled=False)
    workload, total_wei = pipeline.build_workload(cfg, run)
    workload_hash = base.digest(workload)
    if args.resume and manifest['workload_sha256'] != workload_hash:
        raise ValueError('Selected workload changed')
    manifest['workload_sha256'] = workload_hash
    base.write_json(run/'manifest.json', manifest)
    base.write_json(run/'params.json', to_params_dict(cfg))
    paper_data.write_csv(run/'workload.csv', workload, list(workload[0]))
    print(f'Output: {run}', flush=True)
    for number, task in enumerate(manifest['tasks'], 1):
        if task['status'] == 'complete':
            if base.load_completed(run, task, workload) is None:
                raise ValueError('Completed task summary missing')
            continue
        task.pop('error', None)
        task['status'] = 'running'
        base.write_json(run/'manifest.json', manifest)
        print(f'[{number}/{len(tasks)}] {task["id"]}; seed={task["route_seed"]}; {task["token"]}', flush=True)
        try:
            effective = base.task_cfg(cfg, task)
            effective = replace(effective, chain=replace(effective.chain,
                        base_port=base.free_port_block(cfg.chain.base_port, cfg.chain.num_shards)))
            parent = run/'tasks'/task['id']/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            parent.mkdir(parents=True)
            watchdog = RSSWatchdog(float(effective.exp['rss_cap_mb']))
            watchdog.start()
            try:
                name, engine, fund, cap, eps, chi, hl, q_min, frac = pipeline._arm_spec(task['token'])
                stats = pipeline.run_arm(effective, task['tag'], workload, total_wei, parent,
                                         watchdog, engine=engine, fund_eth=fund, cap_mult=cap,
                                         eps=eps, chi=chi, hl=hl, qmin=q_min, frac=frac)
                paper_data.export_arm_data(effective, stats, parent/task['tag'], workload)
            finally:
                watchdog.stop()
            summary = parent/'summary.json'
            base.write_json(summary, dict(task_id=task['id'], workload_sha256=workload_hash,
                            params=to_params_dict(effective), effective_tdr=dict(half_life=hl,
                            q_min=q_min, epsilon=eps), arms=[stats], status={'complete': True}))
            task.update(status='complete', summary=str(summary.relative_to(run)))
            base.write_json(run/'manifest.json', manifest)
            base.assemble(run, manifest, cfg, workload)
        except BaseException as error:
            task.update(status='failed', error=str(error))
            base.write_json(run/'manifest.json', manifest)
            base.assemble(run, manifest, cfg, workload)
            raise
    assert base.assemble(run, manifest, cfg, workload)
    print(f'Completed verified data: {run}', flush=True)
    if not args.no_plot:
        return subprocess.call([sys.executable, str(here/'analyze.py'), '--source', str(run)], cwd=ROOT)
    return 0
