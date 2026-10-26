"""Run a new, frozen, paired five-method experiment; formal default is 100k x 3."""
from __future__ import annotations
import argparse
import copy
import importlib.metadata
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
import yaml
from design import (PROTOCOL,METHODS,FEE_WEI,COST_WEI,read_json,write_json,read_csv,write_csv,
    sha,fingerprint,verify_hashes,validate,build_tasks,effective,summarize)

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
RUNTIME=HERE/'runtime'
TZ=timezone(timedelta(hours=8))
def now():return datetime.now(TZ).isoformat(timespec='seconds')

def clean_env():
    env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
    env.pop('EXP003_RELAY_PROBE',None)
    return env

def free_ports(preferred,count):
    for base in range(preferred,49000-count,count):
        probes=[]
        try:
            for port in range(base,base+count):
                s=socket.socket();probes.append(s)
                if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                s.bind(('127.0.0.1',port))
            return base
        except OSError:pass
        finally:
            for s in probes:s.close()
    raise RuntimeError('No free RPC block; no other processes were stopped')

def lock_socket(port):
    s=socket.socket()
    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:s.bind(('127.0.0.1',port))
    except OSError:
        s.close();raise RuntimeError(f'Runner lock {port} occupied; refusing duplicate execution')
    return s

def imports(run):
    backend=run/'backend';sys.path.insert(0,str(backend))
    from brokerlab.config import load_config,to_params_dict
    from brokerlab.procmon import RSSWatchdog
    from experiments.exp025_regime_shift_sensitivity import pipeline
    from exp040_accounting import verify_and_account,shift_master
    if Path(pipeline.ROOT).resolve()!=backend.resolve():raise RuntimeError('Wrong backend imported')
    return load_config,to_params_dict,RSSWatchdog,pipeline,verify_and_account,shift_master

def resolve(args):
    c=yaml.safe_load(args.config.read_text(encoding='utf-8-sig'))
    trace=Path(c['traffic']['real_csv_path'])
    c['traffic']['real_csv_path']=str((PROJECT/trace).resolve() if not trace.is_absolute() else trace)
    if args.anvil_bin:c['chain']['anvil_bin']=str(Path(args.anvil_bin).resolve())
    else:c['chain']['anvil_bin']=shutil.which('anvil')
    if not c['chain']['anvil_bin']:raise FileNotFoundError('anvil not found')
    if args.smoke:
        c['scale'].update(num_brokers=2,num_users=8)
        c['chain'].update(num_shards=2,num_funded_accounts=210)
        c['exp'].update(transactions=40,ctx_per_broker=20,repetitions=1,rate=10,
            shift_start_index=12,shift_period_transactions=8,shift_cycle_absolute=[1,0],
            pool_scan=8000,pool_scan_max=64000,max_backlog=2000,drain_tail_s=240,
            tdr_ewma_min_blocks=5,profit_broker_idx=0,rss_cap_mb=6000,curve_points=40)
    validate(c,args.smoke)
    if not Path(c['traffic']['real_csv_path']).is_file():raise FileNotFoundError(c['traffic']['real_csv_path'])
    return c

def prepare(args):
    c=resolve(args);tasks=build_tasks(c,args.smoke)
    plan=dict(protocol=PROTOCOL,smoke=args.smoke,methods=list(METHODS),runs_per_method=c['exp']['repetitions'],
        user_CTX_per_run=c['exp']['transactions'],total_runs=len(tasks),
        total_user_CTX=sum(t['transactions'] for t in tasks),brokers=c['scale']['num_brokers'],
        shards=c['chain']['num_shards'],initial_ETH_per_subaccount=45,route_seeds=sorted({t['route_seed'] for t in tasks}),
        fee_per_served_CTX_gwei=FEE_WEI/1e9,cost_per_rebalance_gwei=COST_WEI/1e9)
    print(yaml.safe_dump(plan,sort_keys=False),flush=True)
    if args.dry_run:return None
    run=args.output_root.resolve()/datetime.now(TZ).strftime(('smoke_' if args.smoke else '')+'%Y%m%d_%H%M%S_%f')
    run.mkdir(parents=True);(run/'code').mkdir();(run/'w').mkdir()
    verify_hashes(RUNTIME,read_json(RUNTIME/'provenance.json')['hashes'])
    shutil.copytree(RUNTIME,run/'backend',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['run.py','design.py','plot.py']:
        shutil.copy2(HERE/name,run/'code'/name)
    shutil.copy2(args.config,run/'code/config.yaml')
    write_json(run/'config.json',c)
    write_json(run/'plan.json',plan)
    load_config,_,_,pipe,_,shift=imports(run)
    selection=run/'selection_config.yaml';selection.write_text(yaml.safe_dump(c,sort_keys=False),encoding='utf-8')
    cfg=load_config(selection);master,_=pipe.build_workload(cfg,run)
    e=c['exp'];master=shift(master,e['shift_start_index'],e['shift_period_transactions'],e['shift_cycle_absolute'])
    if len(master)!=e['transactions']:raise ValueError('Incomplete common workload')
    write_csv(run/'w/user_ctx.csv',master)
    manifest=dict(protocol=PROTOCOL,smoke=args.smoke,formal=not args.smoke,created_at=now(),tasks=tasks,plan=plan,
        workload_sha256=sha(run/'w/user_ctx.csv'),trace_sha256=sha(c['traffic']['real_csv_path']),
        config_sha256=sha(run/'config.json'),backend_hashes=fingerprint(run/'backend'),code_hashes=fingerprint(run/'code'),
        anvil_sha256=sha(c['chain']['anvil_bin']),backend_origin=str(RUNTIME),
        lock_port=args.lock_port,base_port=args.base_port)
    write_json(run/'manifest.json',manifest)
    versions={}
    for package in ['web3','eth-account','numpy','matplotlib','psutil','PyYAML']:
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]=None
    write_json(run/'environment.json',dict(python=sys.version,executable=sys.executable,platform=platform.platform(),packages=versions))
    aggregate(run,manifest)
    write_json(args.output_root.resolve()/('latest_smoke.json' if args.smoke else 'latest_run.json'),dict(run=str(run),created_at=now()))
    print('RUN_DIRECTORY='+str(run),flush=True)
    return run

def check_frozen(run,m):
    verify_hashes(run/'backend',m['backend_hashes']);verify_hashes(run/'code',m['code_hashes'])
    if sha(run/'config.json')!=m['config_sha256'] or sha(run/'w/user_ctx.csv')!=m['workload_sha256']:
        raise ValueError('Frozen config or common workload changed')

def validate_task(run,m,t):
    folder=run/t['attempts'][-1]['path'];doc=read_json(folder/'summary.json')
    if doc['protocol']!=PROTOCOL or doc['task_id']!=t['id'] or doc['workload_sha256']!=m['workload_sha256']:
        raise ValueError('Task identity/workload mismatch')
    expected=effective(read_json(run/'config.json'),t,doc['resolved_params']['chain']['base_port'],read_json(run/'config.json')['chain']['anvil_bin'])
    if expected!=doc['effective_config']:raise ValueError('Task parameters changed')
    verify_hashes(folder/t['policy'],doc['artifacts']);verify_hashes(folder,doc['derived'])
    if doc['totals']['assigned']!=t['transactions']:raise ValueError('Task transaction count mismatch')

def worker(run,task_id,folder):
    m=read_json(run/'manifest.json');check_frozen(run,m);t=next(t for t in m['tasks'] if t['id']==task_id)
    if t['status']!='running' or (run/t['attempts'][-1]['path']).resolve()!=folder.resolve():
        raise ValueError('Worker must execute only its active recorded attempt')
    c=read_json(run/'config.json');validate(c,m['smoke'])
    load_config,to_params,Watch,pipe,account,_=imports(run)
    base_port=free_ports(m['base_port'],c['chain']['num_shards'])
    eff=effective(c,t,base_port,c['chain']['anvil_bin'])
    path=folder/'params.yaml';path.write_text(yaml.safe_dump(eff,sort_keys=False),encoding='utf-8')
    cfg=load_config(path);workload=read_csv(run/'w/user_ctx.csv')
    for row in workload:
        for k in ['src_shard','dst_shard','amount_wei','sender_idx','receiver_idx','arrival_pos','original_dst_shard']:row[k]=int(row[k])
    watch=Watch(float(cfg.exp['rss_cap_mb']));watch.start()
    print(f'TASK_BEGIN={t["id"]} ENGINE={t["engine"]} N={len(workload)} SEED={t["route_seed"]} RPC_BASE={base_port}',flush=True)
    print('WORKLOAD_SHA256='+m['workload_sha256'],flush=True)
    try:
        stats=pipe.run_arm(cfg,t['policy'],workload,sum(r['amount_wei'] for r in workload),folder,watch,
            engine=t['engine'],fund_eth=45,eps=eff['exp']['tdr_epsilon'],chi=10,hl=20,qmin=.3)
    finally:watch.stop()
    print(f'TASK_SETTLED={stats["n"]} SERVED={stats["served"]} RELAY={stats["relayed"]} GATES={stats["gates"]}',flush=True)
    write_json(folder/'stats.json',stats)
    arm=folder/t['policy'];rows=read_csv(arm/'ctx_rows.csv');moves=read_csv(arm/'tdr_moves.csv')
    finals,curve=account(eff,t,stats,rows,moves,workload)
    totals=summarize(eff,rows,moves,finals)
    write_csv(folder/'per_broker.csv',finals);write_csv(folder/'revenue_curve.csv',curve)
    write_json(folder/'summary.json',dict(protocol=PROTOCOL,task_id=t['id'],effective_config=eff,resolved_params=to_params(cfg),
        workload_sha256=m['workload_sha256'],stats=stats,totals=totals,
        artifacts={n:sha(arm/n) for n in ['ctx_rows.csv','tdr_moves.csv']},
        derived={n:sha(folder/n) for n in ['per_broker.csv','revenue_curve.csv']}))
    print('TASK_VERIFIED='+task_id,flush=True)

def aggregate(run,m):
    system=[];brokers=[]
    for t in m['tasks']:
        if t['status']!='complete':continue
        folder=run/t['attempts'][-1]['path'];doc=read_json(folder/'summary.json')
        common=dict(task_id=t['id'],method=t['policy'],round=t['round'],seed=t['route_seed'],transactions=t['transactions'])
        system.append(dict(**common,**doc['totals']))
        brokers.extend(dict(**common,**r) for r in read_csv(folder/'per_broker.csv'))
    write_csv(run/'results.csv',system);write_csv(run/'per_broker.csv',brokers)
    write_json(run/'report.json',dict(protocol=PROTOCOL,smoke=m['smoke'],complete=len(system)==len(m['tasks']),
        completed_tasks=len(system),planned_tasks=len(m['tasks']),failed_tasks=[t['id'] for t in m['tasks'] if t['status']=='failed'],
        common_workload_sha256=m['workload_sha256'],all_five_methods_same_runtime=True,
        income_per_broker_served_CTX_gwei=.0021,modeled_cost_per_completed_transfer_gwei=.021))

def progress(run,m,state):
    done=[t for t in m['tasks'] if t['status']=='complete'];active=next((t for t in m['tasks'] if t['status']=='running'),None)
    rows=0;log=None
    if active:
        folder=run/active['attempts'][-1]['path'];log=str(folder/'stdout.log');raw=folder/active['policy']/'ctx_rows.csv'
        try:
            with raw.open('rb') as f:rows=max(0,sum(chunk.count(b'\n') for chunk in iter(lambda:f.read(1024*1024),b''))-1)
        except (FileNotFoundError,PermissionError):pass
    p=dict(updated_at=now(),pid=os.getpid(),stage=state['stage'],error=state.get('error'),completed_tasks=len(done),planned_tasks=len(m['tasks']),
        active_task=active['id'] if active else None,active_CTX_written=rows,active_task_CTX=active['transactions'] if active else None,
        active_log=log,run=str(run))
    write_json(run/'progress.json',p)
    lines=['# Exp046: five methods, 100k CTXs, three rounds','',f"Updated: {p['updated_at']}",f"Stage: {p['stage']}",
        f"Completed: {len(done)}/{len(m['tasks'])}",f"Active: {p['active_task'] or '-'}",f"Written user CTXs: {rows}",
        f"Error: {p['error'] or '-'}",'','| Method | Complete runs |','| --- | ---: |']
    for a,s in METHODS.items():lines.append(f"| {s['label']} | {sum(t['policy']==a for t in done)}/{m['plan']['runs_per_method']} |")
    (run/'progress.md').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')

def execute(run,no_plot=False,max_tasks=None):
    m=read_json(run/'manifest.json')
    if m['protocol']!=PROTOCOL:raise ValueError('Wrong protocol')
    with lock_socket(m['lock_port']):
        check_frozen(run,m)
        expected=build_tasks(read_json(run/'config.json'),m['smoke'])
        keys=['id','round','route_seed','policy','engine','balance_eth','transactions']
        if [[t[k] for k in keys] for t in m['tasks']]!=[[t[k] for k in keys] for t in expected]:
            raise ValueError('Task plan differs from the frozen five-method design')
        for t in m['tasks']:
            if t['status']=='complete':validate_task(run,m,t)
            elif t['status']=='running':
                t['status']='interrupted';t['attempts'][-1]['status']='interrupted'
        write_json(run/'manifest.json',m)
        state=dict(stage='running',error=None);stop=threading.Event()
        def monitor():
            while not stop.is_set():
                try:progress(run,m,state)
                except (OSError,KeyError):pass
                stop.wait(15)
        thread=threading.Thread(target=monitor,daemon=True);thread.start();executed=0
        try:
            for i,t in enumerate(m['tasks']):
                if t['status']=='complete':continue
                if (run/'STOP_AFTER_TASK').exists() or (max_tasks is not None and executed>=max_tasks):
                    state['stage']='stopped_after_task';break
                check_frozen(run,m)
                rel=Path('t')/f'{i:03d}'/f'a{len(t["attempts"])+1:03d}';folder=run/rel;folder.mkdir(parents=True)
                attempt=dict(path=rel.as_posix(),started_at=now(),status='running')
                t['attempts'].append(attempt);t['status']='running';write_json(run/'manifest.json',m)
                print(f'[{i+1}/{len(m["tasks"])}] {t["id"]}',flush=True)
                try:
                    with (folder/'stdout.log').open('w',encoding='utf-8') as out:
                        p=subprocess.run([sys.executable,'-u',str(run/'code/run.py'),'--worker',str(run),t['id'],str(folder)],
                            stdout=out,stderr=subprocess.STDOUT,env=clean_env())
                    if p.returncode:raise RuntimeError(f'Worker exit {p.returncode}; see {folder/"stdout.log"}')
                    validate_task(run,m,t);t['status']=attempt['status']='complete';executed+=1
                except BaseException as err:
                    t['status']=attempt['status']='failed';attempt['error']=repr(err);raise
                finally:
                    attempt['ended_at']=now();write_json(run/'manifest.json',m);aggregate(run,m)
            if all(t['status']=='complete' for t in m['tasks']):
                state['stage']='complete'
                if not no_plot and not m['smoke']:
                    state['stage']='plotting'
                    subprocess.run([sys.executable,str(run/'code/plot.py'),str(run)],check=True)
                    state['stage']='complete'
        except BaseException as err:
            state.update(stage='failed',error=repr(err));raise
        finally:
            stop.set();thread.join(timeout=20);progress(run,m,state)
    return 0

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=HERE/'config.yaml');p.add_argument('--output-root',type=Path,default=HERE/'out')
    p.add_argument('--dry-run',action='store_true');p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--smoke',action='store_true');p.add_argument('--resume',type=Path);p.add_argument('--background',action='store_true')
    p.add_argument('--max-tasks',type=int);p.add_argument('--no-plot',action='store_true');p.add_argument('--anvil-bin')
    p.add_argument('--base-port',type=int,default=40600);p.add_argument('--lock-port',type=int,default=45950)
    p.add_argument('--worker',nargs=3,help=argparse.SUPPRESS);args=p.parse_args(argv)
    if args.worker:
        worker(Path(args.worker[0]),args.worker[1],Path(args.worker[2]));return 0
    if args.max_tasks is not None and args.max_tasks<=0:p.error('--max-tasks must be positive')
    if args.resume:run=args.resume.resolve()
    else:
        with lock_socket(args.lock_port):run=prepare(args)
    if run is None or args.prepare_only:return 0
    if args.background:
        command=[sys.executable,'-u',str(run/'code/run.py'),'--resume',str(run)]
        if args.no_plot:command+=['--no-plot']
        if args.max_tasks:command+=['--max-tasks',str(args.max_tasks)]
        with (run/'runner.log').open('a',encoding='utf-8') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=clean_env(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        write_json(run/'launch.json',dict(pid=proc.pid,command=command,started_at=now()))
        print(f'BACKGROUND_PID={proc.pid}\nRUN_DIRECTORY={run}',flush=True);return 0
    return execute(run,args.no_plot,args.max_tasks)

if __name__=='__main__':raise SystemExit(main())
