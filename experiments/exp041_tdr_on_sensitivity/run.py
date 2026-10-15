"""Shared Anvil execution for separate TDR-on sweeps and TDR-off repetitions."""
from __future__ import annotations
import argparse
import copy
import importlib
import json
import os
import socket
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml
from design import (REVISION, build_tasks, digest, file_hash, read_csv, retained_fee,
                    shift_master, verify_and_account, write_csv, write_json)

HERE = Path(__file__).resolve().parent


def backend_root(explicit=None):
    candidates = [Path(explicit)] if explicit else [HERE.parents[1]]
    for root in candidates:
        if (root/'experiments/exp025_regime_shift_sensitivity/pipeline.py').is_file():
            return root.resolve()
    raise FileNotFoundError('exp025 backend missing; pass --backend-root <complete project>')


def load_backend(root):
    sys.path.insert(0, str(root))
    pipeline = importlib.import_module('experiments.exp025_regime_shift_sensitivity.pipeline')
    config = importlib.import_module('brokerlab.config')
    if Path(pipeline.ROOT).resolve() != root:
        raise RuntimeError('Imported a different backend; use a fresh Python process')
    return pipeline, config


def sources(root, home=HERE):
    files = list(HERE.glob('*.py')) + list(Path(home).glob('*.py')) + list((root/'brokerlab').glob('*.py'))
    for folder in (root/'experiments').iterdir():
        if folder.is_dir():
            files.extend(folder.glob('*.py'))
    return {str(p.resolve()): file_hash(p) for p in sorted(set(files))}


def resolved_config(args, root):
    c = yaml.safe_load(args.config.read_text(encoding='utf-8-sig'))
    for item in args.set:
        key, value = item.split('=',1)
        parts = key.split('.'); target = c
        for part in parts[:-1]:
            target = target[part]
        if parts[-1] not in target:
            raise ValueError(f'Unknown config key: {key}')
        target[parts[-1]] = yaml.safe_load(value)
    if args.rounds is not None:
        c['exp']['repetitions'] = args.rounds
    if args.smoke:
        plain = c['exp']['experiment_kind'] == 'plain_repeats'
        c['chain'].update(num_shards=2, num_funded_accounts=210)
        c['scale'].update(num_brokers=2, num_users=8)
        c['exp'].update(balances_grid_eth=[1] if plain else [1,2], transactions_grid=[40] if plain else [40,80],
                        reference_balance_eth=1, reference_transactions=40, repetitions=2 if plain else 1,
                        master_transactions=80, fig1_bin_width_blocks=10,
                        shift_start_index=12, shift_period_transactions=8,
                        shift_cycle_absolute=[0,1], rate=10, max_backlog=1000,
                        drain_tail_s=240, pool_scan=2000, pool_scan_max=20000,
                        profit_broker_idx=0, rss_cap_mb=4000, curve_points=20)
        c['broker']['initial_balance_eth'] = 1
    c['traffic']['real_csv_path'] = str((root/c['traffic']['real_csv_path']).resolve())
    build_tasks(c, args.only)
    return c


def effective_config(c, t):
    e = copy.deepcopy(c)
    e['broker']['initial_balance_eth'] = t['balance_eth']
    e['tdr']['enabled'] = t['policy'] != 'plain'
    e['exp'].update(balances_eth=t['balance_eth'], route_seed=t['route_seed'],
                    ctx_per_broker=t['transactions']//c['scale']['num_brokers'],
                    arms=t['policy'], shift_to_shard=-1, sota_training_ctx=0)
    return e


def cfg_object(config_module, c, path):
    path.write_text(yaml.safe_dump(c, sort_keys=False), encoding='utf-8')
    return config_module.load_config(path)


def prepare_workloads(c, root, run):
    pipeline, config = load_backend(root)
    d = copy.deepcopy(c)
    n = c['exp']['master_transactions']
    d['exp'].update(ctx_per_broker=n//c['scale']['num_brokers'], shift_to_shard=-1,
                    sota_training_ctx=0, balances_eth=c['exp']['reference_balance_eth'])
    cfg = cfg_object(config,d,run/'selection_config.yaml')
    master,_ = pipeline.build_workload(cfg,run)
    e = c['exp']
    master = shift_master(master,e['shift_start_index'],e['shift_period_transactions'],e['shift_cycle_absolute'])
    directory = run/'w'; directory.mkdir()
    hashes = {}
    for count in sorted(set(c['exp']['transactions_grid'])):
        p = directory/f'{count}.csv'
        write_csv(p,master[:count]); hashes[str(count)] = file_hash(p)
    write_json(run/'workload_design.json',dict(master_transactions=n, nested_prefixes=True,
               shift_start_index=e['shift_start_index'],shift_period_transactions=e['shift_period_transactions'],
               cycle=e['shift_cycle_absolute'],hashes=hashes))
    return hashes


def validate_completed(c, t, run, hashes):
    attempt = run/t['attempts'][-1]['path']
    doc = json.loads((attempt/'summary.json').read_text(encoding='utf-8'))
    if doc['task_id'] != t['id'] or doc['revision'] != REVISION or doc['effective_config'] != effective_config(c,t):
        raise ValueError('Saved task identity/configuration changed')
    workload_path = run/'w'/f'{t["transactions"]}.csv'
    if doc['workload_sha256'] != hashes[str(t['transactions'])]:
        raise ValueError('Saved task workload mismatch')
    arm = attempt/t['policy']
    for name, sha in doc['artifacts'].items():
        if file_hash(arm/name) != sha:
            raise ValueError(f'Saved artifact changed: {arm/name}')
    for name, sha in doc['derived'].items():
        if file_hash(attempt/name) != sha:
            raise ValueError(f'Saved accounting changed: {attempt/name}')
    return verify_and_account(c,t,doc['stats'],read_csv(arm/'ctx_rows.csv'),
                              read_csv(arm/'tdr_moves.csv'),read_csv(workload_path))


def aggregate(c, tasks, run):
    results=[]; brokers=[]
    for t in tasks:
        if t['status'] != 'complete':
            continue
        attempt=run/t['attempts'][-1]['path']
        doc=json.loads((attempt/'summary.json').read_text(encoding='utf-8'))
        aa=read_csv(attempt/'per_broker.csv')
        common=dict(task_id=t['id'],round=t['round'],route_seed=t['route_seed'],
                    balance_eth=t['balance_eth'],transactions=t['transactions'],policy=t['policy'],
                    sweeps=','.join(t['sweeps']))
        income=sum(int(x['income_wei']) for x in aa); upper=sum(int(x['upper_wei']) for x in aa)
        cost=sum(int(x['modeled_cost_wei']) for x in aa)
        st=doc['stats']
        results.append(dict(**common,served=st['served'],relay=st['relayed'],
            tdr_transfers=st['tdr']['transfers_done'],income_wei=income,upper_wei=upper,
            modeled_cost_wei=cost,net_profit_wei=income-cost,income_realization=income/upper,
            throughput=st['throughput_ctx_per_s'],wall_s=st['wall_s'],attempt=str(attempt.relative_to(run))))
        brokers.extend(dict(**common,**a) for a in aa)
    if results:
        write_csv(run/'results.csv',results);write_csv(run/'per_broker.csv',brokers)
    write_json(run/'report.json',dict(experiment=c['label'],kind=c['exp']['experiment_kind'],
        revision=REVISION,complete=len(results)==len(tasks),
        completed_tasks=len(results),planned_tasks=len(tasks),
        failed_tasks=[t['id'] for t in tasks if t['status']=='failed'],
        fee_per_served_ctx_wei=retained_fee(c),
        upper_definition='0.1 * fixed service fee * ALL assigned user CTX, broker + relay; no internal TDR',
        routing='dynamic; each arm has its own actual broker assignment',
        chain_gas_price_wei=0,modeled_cost_not_deducted_from_balances=True))


def execute(c,t,root,run,attempt):
    pipeline, config = load_backend(root)
    from brokerlab.procmon import RSSWatchdog
    from experiments.exp025_regime_shift_sensitivity.runner import free_port_block
    effective=effective_config(c,t)
    cfg=cfg_object(config,effective,attempt/'params.yaml')
    cfg=replace(cfg,chain=replace(cfg.chain,base_port=free_port_block(cfg.chain.base_port,cfg.chain.num_shards)))
    workload_path=run/'w'/f'{t["transactions"]}.csv'
    workload=read_csv(workload_path)
    # Shared pipeline expects numeric input dictionaries, not strings read from CSV.
    for x in workload:
        for key in ('src_shard','dst_shard','amount_wei','sender_idx','receiver_idx','arrival_pos','original_dst_shard'):
            x[key]=int(x[key])
    watchdog=RSSWatchdog(float(cfg.exp['rss_cap_mb']));watchdog.start()
    try:
        stats=pipeline.run_arm(cfg,t['policy'],workload,sum(x['amount_wei'] for x in workload),
            attempt,watchdog,engine=t['policy'],fund_eth=t['balance_eth'],
            eps=c['exp']['tdr_epsilon'],chi=c['exp']['tdr_chi_blocks'],
            hl=c['exp']['tdr_ewma_half_life'],qmin=c['exp']['tdr_q_min'])
    finally:
        watchdog.stop()
    arm=attempt/t['policy']
    rows=read_csv(arm/'ctx_rows.csv');moves=read_csv(arm/'tdr_moves.csv')
    finals,curve=verify_and_account(c,t,stats,rows,moves,workload)
    write_csv(attempt/'per_broker.csv',finals);write_csv(attempt/'revenue_curve.csv',curve)
    artifacts={name:file_hash(arm/name) for name in ('ctx_rows.csv','tdr_moves.csv')}
    write_json(attempt/'summary.json',dict(task_id=t['id'],revision=REVISION,
        effective_config=effective,resolved_params=config.to_params_dict(cfg),
        workload_sha256=file_hash(workload_path),stats=stats,artifacts=artifacts,
        derived={name:file_hash(attempt/name) for name in ('per_broker.csv','revenue_curve.csv')}))


def main(argv=None, home=None):
    home = Path(home).resolve() if home else HERE
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=home/'config.yaml')
    p.add_argument('--backend-root',type=Path)
    p.add_argument('--only',choices=['all','balance','transactions'],default='all')
    p.add_argument('--rounds',type=int);p.add_argument('--set',action='append',default=[])
    p.add_argument('--dry-run',action='store_true');p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--smoke',action='store_true');p.add_argument('--resume',type=Path)
    p.add_argument('--output-root',type=Path,default=home/'out')
    p.add_argument('--max-tasks',type=int,help='Stop after this many NEW tasks; resume the same plan later')
    p.add_argument('--no-plot',action='store_true')
    args=p.parse_args(argv)
    if args.max_tasks is not None and args.max_tasks <= 0:p.error('--max-tasks must be positive')
    root=backend_root(args.backend_root);c=resolved_config(args,root);tasks=build_tasks(c,args.only)
    signature=digest(dict(revision=REVISION,config=c,only=args.only,smoke=args.smoke))
    print(json.dumps(dict(experiment=c['label'],kind=c['exp']['experiment_kind'],backend=str(root),revision=REVISION,smoke=args.smoke,
        formal=not args.smoke and c['exp']['repetitions']==10,
        tasks=len(tasks),total_user_ctx=sum(t['transactions'] for t in tasks),
        fee_per_served_ctx_wei=retained_fee(c),first_task=tasks[0]),ensure_ascii=False,indent=2),flush=True)
    if args.dry_run:return 0
    # Exclusive lock covers preparation and execution; never kill other experiments.
    lock=socket.socket()
    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:lock.bind(('127.0.0.1',c['exp']['runner_lock_port']))
    except OSError as e:raise RuntimeError('Another exp040/exp041 runner is active; run serially') from e
    with lock:
        source_files=sources(root,home)
        if args.resume:
            run=args.resume.resolve();manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
            if manifest['signature'] != signature or manifest['sources'] != source_files:
                raise ValueError('Cannot resume with changed configuration/grid/source code')
            if manifest['trace_sha256'] != file_hash(c['traffic']['real_csv_path']):
                raise ValueError('Trace changed')
            tasks=manifest['tasks'];hashes=manifest['workload_hashes']
            for n,sha in hashes.items():
                if file_hash(run/'w'/f'{n}.csv') != sha:raise ValueError('Saved workload changed')
            for t in tasks:
                if t['status']=='complete':validate_completed(c,t,run,hashes)
        else:
            run=args.output_root.resolve()/datetime.now().strftime('%Y%m%d_%H%M%S_%f');run.mkdir(parents=True)
            write_json(run/'config.json',c)
            hashes=prepare_workloads(c,root,run)
            manifest=dict(experiment=c['label'],kind=c['exp']['experiment_kind'],revision=REVISION,signature=signature,sources=source_files,
                smoke=args.smoke,formal=not args.smoke and c['exp']['repetitions']==10,
                trace_sha256=file_hash(c['traffic']['real_csv_path']),workload_hashes=hashes,tasks=tasks)
            write_json(run/'manifest.json',manifest)
        print(f'RUN_DIRECTORY={run}',flush=True)
        aggregate(c,tasks,run)
        if args.prepare_only:return 0
        executed=0
        for i,t in enumerate(tasks):
            if t['status']=='complete':continue
            if args.max_tasks is not None and executed>=args.max_tasks:break
            if sources(root,home)!=source_files:raise ValueError('Source changed during experiment')
            path=Path('t')/f'{i:04d}'/f'a{len(t["attempts"])+1:03d}'
            attempt=run/path;attempt.mkdir(parents=True)
            record=dict(path=str(path),status='running',started_at=datetime.now().isoformat())
            t['attempts'].append(record);t['status']='running';write_json(run/'manifest.json',manifest)
            print(f'[{i+1}/{len(tasks)}] {t["id"]}',flush=True)
            try:
                execute(c,t,root,run,attempt)
                if sources(root,home)!=source_files:raise ValueError('Source changed during task')
                record['status']='complete';t['status']='complete';executed+=1
            except BaseException as error:
                record.update(status='failed',error=f'{type(error).__name__}: {error}')
                t['status']='failed';write_json(attempt/'failure.json',record)
                raise
            finally:
                record['ended_at']=datetime.now().isoformat();write_json(run/'manifest.json',manifest)
                aggregate(c,tasks,run)
        if not args.no_plot and all(t['status']=='complete' for t in tasks):
            from analyze import analyze
            analyze(run)
        elif not args.no_plot:
            print('Plan incomplete; raw data saved. Resume before formal plots, or analyze.py --allow-partial.',flush=True)
        return 0


if __name__=='__main__':
    raise SystemExit(main())
