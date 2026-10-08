"""Isolated epsilon=1 endpoint, serial execution and resumable result gates."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import psutil
import yaml

from brokerlab.config import load_config,apply_overrides,to_params_dict
from brokerlab.procmon import RSSWatchdog
from experiments.exp025_regime_shift_sensitivity import runner as base,paper_data
from experiments.exp029_added_parameter_sensitivity import pipeline

ROOT=Path(__file__).resolve().parents[2]
HALF_LIVES=[2,4,8,10,20,50,100,200,400,800,1000]

def source_fingerprint():
    files=[Path(__file__),Path(__file__).with_name('run.py'),Path(pipeline.__file__),
           Path(__file__).with_name('tdr_engine.py'),Path(__file__).with_name('tdr_hook.py'),
           Path(base.__file__),Path(paper_data.__file__)]
    files+=sorted((ROOT/'brokerlab').glob('*.py'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

def make_tasks(cfg,space,order_seed=2942):
    tasks=[]
    for rep in range(1,int(cfg.exp['repetitions'])+1):
        current=[]
        for sweep,spec in space.items():
            if sweep not in {'epsilon','q_min','half_life'}:raise ValueError('Unsupported sweep')
            values=spec['values']
            if not values or len(values)!=len(set(values)):raise ValueError('Empty or duplicate values')
            for value in values:
                state=dict(q_min=.3,epsilon=.95,half_life=20)
                state.update(spec['fixed']);state[sweep]=value
                if not all(math.isfinite(float(v)) for v in state.values()):raise ValueError('Nonfinite setting')
                if not 0<=state['q_min']<=1 or not 0<=state['epsilon']<=1 or state['half_life']<=0:
                    raise ValueError(f'Invalid setting: {state}')
                token=f"topup@{cfg.exp['balances_eth']}@@{state['epsilon']}@@{state['half_life']}@{state['q_min']}"
                current.append(dict(id=f'{sweep}_{value}_r{rep}',sweep=sweep,value=value,round=rep,
                              route_seed=int(cfg.exp['route_seed'])+(rep-1)*int(cfg.exp['route_seed_step']),
                              token=token,tag=base.token_tag(token),effective_tdr=state,status='pending'))
        random.Random(order_seed+rep).shuffle(current);tasks.extend(current)
    return tasks

def wait_for_processes(pids,run,manifest):
    tracked={}
    for pid in pids:
        try:
            parent=psutil.Process(pid)
            for p in [parent]+parent.children(recursive=True):
                try:tracked[p.pid]=p.create_time()
                except psutil.NoSuchProcess:pass
        except psutil.NoSuchProcess:pass
    if not tracked:return
    manifest['execution_status']='waiting_for_existing_experiment'
    manifest['wait_processes']={str(k):v for k,v in tracked.items()}
    base.write_json(run/'manifest.json',manifest)
    last_log=0
    while True:
        live=[]
        for pid,created in tracked.items():
            try:
                p=psutil.Process(pid)
                if p.create_time()==created and p.is_running() and p.status()!=psutil.STATUS_ZOMBIE:
                    live.append(pid)
            except psutil.NoSuchProcess:pass
        if not live:break
        if time.monotonic()-last_log>=60:
            print(f'Waiting for existing experiment ({len(live)} tracked processes alive); no new chains started.',flush=True)
            last_log=time.monotonic()
        time.sleep(5)
    print('Existing experiment finished; starting exp029.',flush=True)

def main(here):
    parser=argparse.ArgumentParser(description='exp029: added epsilon/q_min points, ten rounds by default')
    parser.add_argument('--config',type=Path,default=here/'config.yaml')
    parser.add_argument('--space',type=Path,default=here/'search_space.yaml')
    parser.add_argument('--include-half-life',action='store_true',help='Also repeat exp028 eleven half-life points')
    parser.add_argument('--only',help='Comma-separated dimensions')
    parser.add_argument('--epsilon-one-only',action='store_true',help='Run only exact epsilon=1 with half-life=20 and q_min=0.3')
    parser.add_argument('--rounds',type=int,default=10,help='Repetitions per point; default 10; use 1 for a preliminary run')
    parser.add_argument('--order-seed',type=int,default=2942)
    parser.add_argument('--set',action='append',default=[])
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--output-root',type=Path,default=here/'out')
    parser.add_argument('--wait-for-pid',type=int,action='append',default=[])
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--no-plot',action='store_true')
    args=parser.parse_args()
    if args.rounds<1:parser.error('At least one round is required')
    cfg=apply_overrides(load_config(args.config),args.set)
    cfg=apply_overrides(cfg,[f'exp.repetitions={args.rounds}','exp.tdr_ewma_window_blocks=0','exp.tdr_ewma_two_sided=false'])
    space=yaml.safe_load(args.space.read_text(encoding='utf-8'))
    if args.include_half_life:space['half_life']={'values':HALF_LIVES,'fixed':{'q_min':.3,'epsilon':.95}}
    if args.only:
        names=args.only.split(',')
        if set(names)-set(space):parser.error('Unknown sweep dimension')
        space={name:space[name] for name in names}
    if args.epsilon_one_only:
        if args.include_half_life or (args.only and args.only!='epsilon'):
            parser.error('--epsilon-one-only cannot be combined with other sweep dimensions')
        space={'epsilon':{'values':[1.0],'fixed':{'q_min':0.3,'half_life':20}}}
    if args.smoke:
        cfg=replace(cfg,scale=replace(cfg.scale,num_brokers=1),broker=replace(cfg.broker,initial_balance_eth=5))
        cfg=apply_overrides(cfg,['exp.repetitions=1','exp.ctx_per_broker=80','exp.balances_eth=5','exp.rate=5','exp.drain_tail_s=240'])
        space={k:{**s,'values':([s['values'][0],1.0] if k=='epsilon' and s['values'][0]!=1.0 else s['values'][:1])} for k,s in space.items()}
    expected=cfg.scale.num_brokers*int(cfg.exp['ctx_per_broker'])
    if not args.smoke and expected!=40000:parser.error('Formal exp029 requires 40000 CTX per run')
    tasks=make_tasks(cfg,space,args.order_seed)
    signature=base.digest(dict(params=to_params_dict(cfg),space=space,order_seed=args.order_seed,smoke=args.smoke))
    print(f'exp029: {len(tasks)} runs x {expected:,} CTX = {len(tasks)*expected:,}; serial; smoke={args.smoke}',flush=True)
    if args.dry_run:
        print(json.dumps(dict(sweep_space=space,rounds=cfg.exp['repetitions'],planned_tasks=len(tasks),
                             first_tasks=tasks[:3],epsilon_endpoint_exact=True,wait_for_pids=args.wait_for_pid),indent=2))
        return 0
    run=args.resume.resolve() if args.resume else args.output_root.resolve()/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    if args.resume:
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        if manifest['signature']!=signature or manifest['source_files']!=source_fingerprint():
            raise ValueError('Resume configuration/grid/execution source changed')
    else:
        run.mkdir(parents=True)
        manifest=dict(experiment='exp029',smoke=args.smoke,signature=signature,source_files=source_fingerprint(),
                      total_ctx_per_arm=expected,repetitions=cfg.exp['repetitions'],sweep_space=space,tasks=tasks,
                      execution_status='preparing',historical_data_pooled=False,runner_pid=os.getpid(),
                      epsilon_one='Exact single-sided Top-up no-trigger boundary; demand observation retained')
    workload,total_wei=pipeline.build_workload(cfg,run);workload_hash=base.digest(workload)
    if args.resume and manifest['workload_sha256']!=workload_hash:raise ValueError('Workload changed')
    manifest['workload_sha256']=workload_hash
    manifest['runner_pid']=os.getpid()
    base.write_json(run/'manifest.json',manifest);base.write_json(run/'params.json',to_params_dict(cfg))
    paper_data.write_csv(run/'workload.csv',workload,list(workload[0]))
    print(f'Output: {run}',flush=True)
    wait_for_processes(args.wait_for_pid,run,manifest)
    if manifest['source_files']!=source_fingerprint():raise ValueError('Sources changed while queued; restart before any trial')
    manifest['execution_status']='running';base.write_json(run/'manifest.json',manifest)
    for number,task in enumerate(manifest['tasks'],1):
        if task['status']=='complete':
            if base.load_completed(run,task,workload) is None:raise ValueError('Missing completed summary')
            continue
        task.pop('error',None);task['status']='running';base.write_json(run/'manifest.json',manifest)
        print(f'[{number}/{len(tasks)}] {task["id"]}; seed={task["route_seed"]}; {task["token"]}',flush=True)
        try:
            effective=base.task_cfg(cfg,task)
            effective=replace(effective,chain=replace(effective.chain,base_port=base.free_port_block(cfg.chain.base_port,cfg.chain.num_shards)))
            parent=run/'tasks'/task['id']/datetime.now().strftime('%Y%m%d_%H%M%S_%f');parent.mkdir(parents=True)
            watchdog=RSSWatchdog(float(effective.exp['rss_cap_mb']));watchdog.start()
            try:
                name,engine,fund,cap,eps,chi,hl,q_min,frac=pipeline._arm_spec(task['token'])
                stats=pipeline.run_arm(effective,task['tag'],workload,total_wei,parent,watchdog,
                      engine=engine,fund_eth=fund,cap_mult=cap,eps=eps,chi=chi,hl=hl,qmin=q_min,frac=frac)
                if eps==1.0 and stats['tdr']['transfers_done']!=0:raise ValueError('epsilon=1 boundary unexpectedly moved funds')
                paper_data.export_arm_data(effective,stats,parent/task['tag'],workload)
            finally:watchdog.stop()
            summary=parent/'summary.json'
            base.write_json(summary,dict(task_id=task['id'],workload_sha256=workload_hash,
                            params=to_params_dict(effective),effective_tdr=task['effective_tdr'],
                            arms=[stats],status={'complete':True}))
            task.update(status='complete',summary=str(summary.relative_to(run)))
            base.write_json(run/'manifest.json',manifest);base.assemble(run,manifest,cfg,workload)
        except BaseException as error:
            task.update(status='failed',error=str(error));manifest['execution_status']='failed'
            base.write_json(run/'manifest.json',manifest);base.assemble(run,manifest,cfg,workload);raise
    assert base.assemble(run,manifest,cfg,workload)
    manifest['execution_status']='complete';base.write_json(run/'manifest.json',manifest)
    print(f'Completed verified data: {run}',flush=True)
    if not args.no_plot:return subprocess.call([sys.executable,str(here/'analyze.py'),'--source',str(run)],cwd=ROOT)
    return 0
