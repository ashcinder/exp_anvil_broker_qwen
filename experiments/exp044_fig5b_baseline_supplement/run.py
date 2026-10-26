"""Matched Autoloop/Spider supplement; never rerun or alter the exp040 baseline."""
from __future__ import annotations
import argparse,copy,datetime,hashlib,json,os,shutil,socket,sys,threading,time,traceback
from pathlib import Path

HERE=Path(__file__).resolve().parent
DEFAULT_ROOT=Path('D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen')
METHODS=('autoloop','spider')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--output',type=Path,default=HERE/'out/matched_transactions')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--max-tasks',type=int)
    args=p.parse_args();root=args.backend_root.resolve()
    base=(args.baseline or root/'experiments/exp040_liquidity_scale/out/20261016_080212_748363').resolve()
    sys.path.insert(0,str(root/'experiments/exp040_liquidity_scale'))
    import run as original
    from design import file_hash,write_json,write_csv,read_csv,verify_and_account
    baseline=json.loads((base/'manifest.json').read_text());c=json.loads((base/'config.json').read_text())
    assert not baseline['smoke'] and all(t['status']=='complete' for t in baseline['tasks'])
    # Compare every original Python source except the post-run plotting-only script.
    for name,h in baseline['sources'].items():
        path=Path(name)
        if path==root/'experiments/exp040_liquidity_scale/analyze.py':continue
        if not path.exists() or file_hash(path)!=h:raise ValueError('Original runtime source changed: '+name)
    tasks=[]
    print('Validating original 80 tasks and frozen inputs...',flush=True)
    for t in baseline['tasks']:
        original.validate_completed(c,t,base,baseline['workload_hashes'])
        for method in (METHODS if t['round']%2 else METHODS[::-1]):
            item=copy.deepcopy(t);item.update(id=t['id'].rsplit('_',1)[0]+'_'+method,policy=method,status='pending',attempts=[])
            tasks.append(item)
    print(json.dumps(dict(tasks=len(tasks),methods=METHODS,transactions=c['exp']['transactions_grid'],rounds=10,balance_eth=45,total_user_ctx=sum(t['transactions'] for t in tasks),baseline=str(base)),indent=2),flush=True)
    if args.dry_run:return
    lock=socket.socket()
    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    lock.bind(('127.0.0.1',c['exp']['runner_lock_port']))
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    source_paths=[HERE/'run.py']+list((root/'brokerlab').glob('*.py'))+list((root/'experiments/exp025_regime_shift_sensitivity').glob('*.py'))+[root/'experiments/exp040_liquidity_scale'/n for n in ('run.py','design.py')]
    hashes={str(q):file_hash(q) for q in source_paths}
    signature=dict(baseline_manifest_sha256=file_hash(base/'manifest.json'),baseline_config_sha256=file_hash(base/'config.json'),sources=hashes,config=c)
    manifest_path=out/'manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        if manifest['signature']!=signature:raise ValueError('Supplement sources/config/baseline changed; do not mix results')
        tasks=manifest['tasks']
        for t in tasks:
            if t['status']=='complete':original.validate_completed(c,t,out,baseline['workload_hashes'])
    else:
        (out/'w').mkdir(exist_ok=True)
        for n,h in baseline['workload_hashes'].items():
            src=base/'w'/f'{n}.csv';assert file_hash(src)==h
            shutil.copy2(src,out/'w'/src.name)
        manifest=dict(signature=signature,tasks=tasks,methods=list(METHODS),baseline=str(base),workload_hashes=baseline['workload_hashes'])
        write_json(manifest_path,manifest);write_json(out/'config.json',c)
    state={'stage':'running','error':''};stop=threading.Event()
    def report():
        while True:
            done=[t for t in tasks if t['status']=='complete'];active=[t for t in tasks if t['status']=='running'];now=datetime.datetime.now().astimezone()
            lines=['# Fig5b：Autoloop / Spider补充实验','',f'更新时间：{now:%Y-%m-%d %H:%M:%S %z}（每30秒刷新）',f"状态：{state['stage']}",f'已完成：{len(done)}/{len(tasks)}',f"当前任务：{', '.join(t['id'] for t in active) or '无'}",f"错误：{state['error'] or '无'}",'', '原With TDR和潜在收益结果只读复用；每个观察值对应broker 19全部子账户。','', '| 用户交易量 | Autoloop | Spider-rebalance |','|---:|---:|---:|']
            for n in c['exp']['transactions_grid']:
                counts=[sum(t['transactions']==n and t['policy']==method for t in done) for method in METHODS]
                lines.append(f'| {n} | {counts[0]}/10 | {counts[1]}/10 |')
            (out/'实时进度.md').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
            if stop.wait(30):break
    monitor=threading.Thread(target=report,daemon=True);monitor.start()
    def aggregate():
        result=[]
        for t in tasks:
            if t['status']!='complete':continue
            folder=out/t['attempts'][-1]['path']
            for r in read_csv(folder/'per_broker.csv'):result.append(dict(task_id=t['id'],round=t['round'],route_seed=t['route_seed'],transactions=t['transactions'],policy=t['policy'],**r))
        if result:write_csv(out/'per_broker.csv',result)
    try:
        executed=0
        for i,t in enumerate(tasks):
            if t['status']=='complete':continue
            if args.max_tasks is not None and executed>=args.max_tasks:break
            if any(file_hash(Path(n))!=h for n,h in hashes.items()):raise ValueError('Supplement runtime source changed')
            path=Path('t')/f'{i:04d}'/f'a{len(t["attempts"])+1:03d}';folder=out/path;folder.mkdir(parents=True)
            a=dict(path=str(path),status='running',started_at=datetime.datetime.now().isoformat());t['attempts'].append(a);t['status']='running';write_json(manifest_path,manifest)
            print(f'[{i+1}/{len(tasks)}] {t["id"]}',flush=True)
            try:
                original.execute(c,t,root,out,folder)
                if any(file_hash(Path(n))!=h for n,h in hashes.items()):raise ValueError('Supplement runtime source changed')
                a['status']=t['status']='complete';executed+=1
            except BaseException as err:
                a['status']=t['status']='failed';a['error']=repr(err);raise
            finally:
                a['ended_at']=datetime.datetime.now().isoformat();write_json(manifest_path,manifest);aggregate()
        state['stage']='complete' if all(t['status']=='complete' for t in tasks) else 'stopped at requested task limit'
        if state['stage']=='complete':
            from analyze import analyze
            analyze(out)
    except BaseException as err:
        state.update(stage='failed',error=repr(err));raise
    finally:
        stop.set();monitor.join();report_final=threading.Thread(target=report);report_final.start();report_final.join();lock.close()

if __name__=='__main__':main()
