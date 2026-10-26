"""Seven-balance, four-policy matched experiment at 100k user CTX per run."""
from __future__ import annotations
from pathlib import Path
import argparse,copy,csv,gzip,hashlib,io,json,os,shutil,socket,subprocess,sys,threading,time,traceback
from datetime import datetime
from collections import Counter

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
BASE=HERE/'reference'
RUNTIME=PROJECT/'experiments/exp045_plain_sensitivity_supplement/runtime/c'
BALANCES=[15,30,45,75,100,150,200]
METHODS=['topup','plain','autoloop','spider']
PROTOCOL='exp047-balance-100k-four-methods-v1'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
def rows(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(p,data):
    if not data:return
    p=Path(p);tmp=p.with_suffix('.tmp')
    with tmp.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    tmp.replace(p)
def fingerprint(root):return {str(p.relative_to(root)).replace('\\','/'):sha(p) for p in root.rglob('*.py')}
def check(root,hashes):
    for n,h in hashes.items():
        if sha(root/n)!=h:raise ValueError('Frozen evidence changed: '+str(root/n))
def plan(smoke=False,start_round=1,rounds=None):
    if rounds is None:rounds=1 if smoke else 10
    if start_round<1:raise ValueError('--start-round must be >= 1')
    if rounds<1:raise ValueError('--rounds must be >= 1')
    tasks=[]
    for rep in range(start_round,start_round+rounds):
        cells=[15] if smoke else BALANCES
        offset=(rep-1)%len(cells);cells=cells[offset:]+cells[:offset]
        order=METHODS[(rep-1)%4:]+METHODS[:(rep-1)%4]
        for b in cells:
            for method in order:
                n=2000 if smoke else 100000
                tasks.append(dict(id=f'r{rep:02d}_b{b}_n{n}_{method}',round=rep,route_seed=7+(rep-1)*10,balance_eth=b,transactions=n,policy=method,status='pending',attempts=[]))
    return tasks
def effective(c,t,m):
    c=copy.deepcopy(c);c['label']='exp047_balance_100k'
    c['broker']['initial_balance_eth']=t['balance_eth'];c['tdr']['enabled']=t['policy']!='plain'
    c['chain'].update(base_port=m['base_port'],anvil_bin=m['anvil_bin'])
    c['exp'].update(arms=t['policy'],balances_eth=t['balance_eth'],route_seed=t['route_seed'],ctx_per_broker=t['transactions']//50,sota_training_ctx=0,shift_to_shard=-1,
        sota_autoloop_low=.3,sota_autoloop_high=.7,sota_capacity_mult=2.,sota_spider_vol_mult=.25,sota_check_blocks=10,sota_conf_blocks=10)
    return c
def free_ports(start,n):
    for base in range(start,49000-n,n):
        held=[]
        try:
            for port in range(base,base+n):
                s=socket.socket();held.append(s)
                if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                s.bind(('127.0.0.1',port))
            return base
        except OSError:pass
        finally:
            for s in held:s.close()
    raise RuntimeError('No free RPC port block')
def worker(run,task_id,folder):
    import yaml
    from dataclasses import replace
    m=read(run/'manifest.json');check(run/'backend',m['backend_hashes']);check(run/'code',m['code_hashes'])
    if sha(run/'w/workload.csv')!=m['workload_sha256']:raise ValueError('Workload changed')
    t=next(t for t in m['tasks'] if t['id']==task_id);c=effective(read(run/'base_config.json'),t,m)
    sys.path.insert(0,str(run/'backend'))
    from brokerlab.config import load_config,to_params_dict
    from brokerlab.procmon import RSSWatchdog
    from experiments.exp025_regime_shift_sensitivity import pipeline
    from exp040_accounting import verify_and_account
    data=rows(run/'w/workload.csv')
    for r in data:
        for k in ('src_shard','dst_shard','amount_wei','sender_idx','receiver_idx','arrival_pos','original_dst_shard'):r[k]=int(r[k])
    assert len(data)==t['transactions']
    params=folder/'params.yaml';params.write_text(yaml.safe_dump(c,sort_keys=False),encoding='utf-8')
    cfg=load_config(params);cfg=replace(cfg,chain=replace(cfg.chain,base_port=free_ports(m['base_port'],16)))
    wd=RSSWatchdog(float(c['exp']['rss_cap_mb']));wd.start()
    try:
        stats=pipeline.run_arm(cfg,t['policy'],data,sum(r['amount_wei'] for r in data),folder,wd,engine=t['policy'],fund_eth=t['balance_eth'],eps=.95,chi=10,hl=20,qmin=.3)
    finally:wd.stop()
    arm=folder/t['policy'];raw=rows(arm/'ctx_rows.csv');moves=rows(arm/'tdr_moves.csv')
    finals,curve=verify_and_account(c,t,stats,raw,moves,data)
    if t['policy']=='plain' and (moves or stats['tdr']['events_opened']):raise ValueError('Plain unexpectedly rebalanced')
    for r in finals:
        assert r['net_profit_wei']==r['served']*2100000-r['tdr_transfers']*21000000
    write_csv(folder/'per_broker.csv',finals);write_csv(folder/'revenue_curve.csv',curve)
    save(folder/'summary.json',dict(protocol=PROTOCOL,task_id=t['id'],effective_config=c,resolved_params=to_params_dict(cfg),stats=stats,workload_sha256=m['workload_sha256'],artifacts={n:sha(arm/n) for n in ('ctx_rows.csv','tdr_moves.csv')},derived={n:sha(folder/n) for n in ('per_broker.csv','revenue_curve.csv')}))
    print('TASK_VERIFIED='+task_id,flush=True)
def validate_task(run,m,t):
    p=run/t['attempts'][-1]['path'];d=read(p/'summary.json')
    assert d['protocol']==PROTOCOL and d['task_id']==t['id']
    assert d['effective_config']==effective(read(run/'base_config.json'),t,m)
    assert d['workload_sha256']==m['workload_sha256']
    check(p/t['policy'],d['artifacts']);check(p,d['derived'])
def aggregate(run,m):
    records=[]
    for t in m['tasks']:
        if t['status']!='complete':continue
        for r in rows(run/t['attempts'][-1]['path']/'per_broker.csv'):
            records.append(dict(task_id=t['id'],round=t['round'],route_seed=t['route_seed'],balance_eth=t['balance_eth'],transactions=t['transactions'],policy=t['policy'],**r))
    write_csv(run/'per_broker.csv',records)
    save(run/'report.json',dict(protocol=PROTOCOL,smoke=m['smoke'],complete=all(t['status']=='complete' for t in m['tasks']),counts=dict(Counter(t['status'] for t in m['tasks'])),tasks=len(m['tasks'])))
def execute(run):
    m=read(run/'manifest.json');check(run/'backend',m['backend_hashes']);check(run/'code',m['code_hashes'])
    assert sha(run/'w/workload.csv')==m['workload_sha256']
    for t in m['tasks']:
        if t['status']=='complete':validate_task(run,m,t)
    lock=socket.socket()
    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    lock.bind(('127.0.0.1',m['lock_port']))
    state={'status':'running','error':''};stop=threading.Event()
    def report():
        while True:
            ts=m['tasks'];active=[t['id'] for t in ts if t['status']=='running'];counts=Counter(t['status'] for t in ts)
            text=['# 初始余额利润实验：每轮100,000笔用户CTX','',f'更新时间：{datetime.now():%Y-%m-%d %H:%M:%S}（Asia/Shanghai，每15秒刷新）',f"状态：{state['status']}",f"完成：{counts['complete']}/{len(ts)}",f"当前：{', '.join(active) or '无'}",f"错误：{state['error'] or '无'}",f"Smoke：{m['smoke']}",'','| 每子账户ETH | With TDR | No Rebalancing | Autoloop | Spider |','|---:|---:|---:|---:|---:|']
            for b in sorted({t['balance_eth'] for t in ts}):text.append('| '+str(b)+' | '+' | '.join(str(sum(t['status']=='complete' and t['balance_eth']==b and t['policy']==k for t in ts)) for k in METHODS)+' |')
            tmp=run/'progress.tmp';tmp.write_text('\n'.join(text)+'\n',encoding='utf-8-sig');tmp.replace(run/'progress.md')
            if stop.wait(15):return
    monitor=threading.Thread(target=report,daemon=True);monitor.start()
    try:
        for i,t in enumerate(m['tasks']):
            if t['status']=='complete':continue
            if (run/'STOP_AFTER_TASK').exists():state['status']='stopped after task';break
            check(run/'backend',m['backend_hashes']);check(run/'code',m['code_hashes'])
            folder=run/'t'/f'{i:04d}'/f'a{len(t["attempts"])+1:03d}';folder.mkdir(parents=True)
            a=dict(path=folder.relative_to(run).as_posix(),status='running',started_at=datetime.now().isoformat());t['attempts'].append(a);t['status']='running';save(run/'manifest.json',m)
            print(f'[{i+1}/{len(m["tasks"])}] {t["id"]}',flush=True)
            try:
                env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1');env.pop('EXP003_RELAY_PROBE',None)
                with (folder/'stdout.log').open('a',encoding='utf-8') as f:
                    p=subprocess.run([sys.executable,'-u',str(run/'code/run.py'),'--worker',str(run),t['id'],str(folder)],stdout=f,stderr=subprocess.STDOUT,env=env)
                if p.returncode:raise RuntimeError(f'Worker exit {p.returncode}: {folder}/stdout.log')
                validate_task(run,m,t);t['status']=a['status']='complete'
            except BaseException as err:t['status']=a['status']='failed';a['error']=repr(err);raise
            finally:a['ended_at']=datetime.now().isoformat();save(run/'manifest.json',m);aggregate(run,m)
        if all(t['status']=='complete' for t in m['tasks']):
            if not m['smoke']:
                sys.path.insert(0,str(run/'code'));from plot import plot
                plot(run)
            state['status']='complete'
    except BaseException as err:state.update(status='failed',error=repr(err));raise
    finally:
        stop.set();monitor.join();report();lock.close()
def prepare(args):
    rounds=args.rounds if args.rounds is not None else (1 if args.smoke else 10)
    tasks=plan(args.smoke,args.start_round,rounds);bm=read(BASE/'manifest.json');c=read(BASE/'config.json');source=BASE/'workload.csv.gz'
    with gzip.open(source,'rb') as f:workload_bytes=f.read()
    workload_hash=hashlib.sha256(workload_bytes).hexdigest()
    if workload_hash!=bm['workload_sha256']:raise ValueError('Reference workload hash mismatch')
    prov=read(RUNTIME/'provenance.json');check(RUNTIME,prov['hashes'])
    c['exp'].update(reference_transactions=100000,transactions_grid=[100000],balances_grid_eth=BALANCES,repetitions=rounds)
    description=dict(protocol=PROTOCOL,smoke=args.smoke,rounds=list(range(args.start_round,args.start_round+rounds)),tasks=len(tasks),user_ctx_per_task=tasks[0]['transactions'],total_user_ctx=sum(t['transactions'] for t in tasks),balances=sorted({t['balance_eth'] for t in tasks}),methods=METHODS,route_seeds=sorted({t['route_seed'] for t in tasks}),workload_sha256=workload_hash,runtime='Historical Fig5c original bytes; all four policies share it')
    print(json.dumps(description,ensure_ascii=False,indent=2),flush=True)
    if args.dry_run:return
    run=(args.output or HERE/'out'/('smoke_2000' if args.smoke else 'formal_100k')).resolve()
    if run.exists():raise FileExistsError('Use --resume '+str(run))
    run.mkdir(parents=True);shutil.copytree(RUNTIME,run/'backend',ignore=shutil.ignore_patterns('__pycache__'))
    (run/'code').mkdir()
    for name in ('run.py','plot.py'):shutil.copy2(HERE/name,run/'code'/name)
    (run/'w').mkdir()
    if args.smoke:write_csv(run/'w/workload.csv',list(csv.DictReader(io.StringIO(workload_bytes.decode('utf-8-sig'))))[:2000])
    else:(run/'w/workload.csv').write_bytes(workload_bytes)
    anvil=args.anvil_bin or shutil.which('anvil') or str(Path.home()/'.foundry/bin/anvil.exe')
    if not Path(anvil).is_file():raise FileNotFoundError('Install Anvil or pass --anvil-bin PATH')
    m=dict(protocol=PROTOCOL,smoke=args.smoke,tasks=tasks,plan=description,base_port=args.base_port,lock_port=args.lock_port,anvil_bin=str(Path(anvil).resolve()),backend_hashes=fingerprint(run/'backend'),code_hashes=fingerprint(run/'code'),workload_sha256=sha(run/'w/workload.csv'),source_workload=str(source),base_config_sha256=sha(BASE/'config.json'))
    save(run/'manifest.json',m);save(run/'base_config.json',c);save(run/'environment.json',dict(python=sys.version,anvil_sha256=sha(anvil)))
    return run
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--smoke',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--output',type=Path);p.add_argument('--resume',type=Path);p.add_argument('--start-round',type=int,default=1,help='First round number; route seed follows the original schedule (round 1=7, round 9=87)');p.add_argument('--rounds',type=int,default=None,help='Number of rounds to schedule (formal default 10; smoke default 1)');p.add_argument('--base-port',type=int,default=42600,help='First RPC port to search for 16 free shard ports');p.add_argument('--lock-port',type=int,default=45954,help='Runner lock port; use a different port for an independent concurrent run');p.add_argument('--anvil-bin',type=str,help='Path to anvil executable (default: PATH or ~/.foundry/bin/anvil.exe)');p.add_argument('--worker',nargs=3);a=p.parse_args()
    if a.worker:return worker(Path(a.worker[0]),a.worker[1],Path(a.worker[2]))
    if a.resume and (a.start_round!=1 or a.rounds is not None):p.error('--start-round/--rounds define a new run; --resume follows the saved task manifest')
    run=a.resume.resolve() if a.resume else prepare(a)
    if run:execute(run)
if __name__=='__main__':main()
