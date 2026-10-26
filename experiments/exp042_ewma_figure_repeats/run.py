"""Independent 10-round EWMA/Plain study producing Fig1a, Fig1b and Fig5a."""
import argparse,copy,importlib,json,socket,sys,subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import replace
import yaml
from design import REVISION,sha,read,write,save,plan,audit_task
from staging import stage

HERE=Path(__file__).resolve().parent
def backend(explicit):
    for p in ([Path(explicit)] if explicit else [HERE.parents[1],Path('D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen')]):
        if (p/'experiments/exp025_regime_shift_sensitivity/pipeline.py').exists():return p.resolve()
    raise FileNotFoundError('Complete backend missing; pass --backend-root PATH')
def free_ports(first,count):
    for base in range(first,49000-count,count):
        probes=[]
        try:
            for port in range(base,base+count):
                s=socket.socket();probes.append(s);s.bind(('127.0.0.1',port))
            return base
        except OSError:pass
        finally:
            for s in probes:s.close()
    raise RuntimeError('No free Anvil port block')
def code_hashes():return {p.name:sha(p) for p in HERE.glob('*.py')}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=HERE/'config.yaml')
    p.add_argument('--backend-root');p.add_argument('--rounds',type=int)
    p.add_argument('--smoke',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--prepare-only',action='store_true');p.add_argument('--no-plot',action='store_true')
    p.add_argument('--fresh-workload',action='store_true');p.add_argument('--resume',type=Path)
    p.add_argument('--output-root',type=Path,default=HERE/'out')
    args=p.parse_args();root=backend(args.backend_root)
    c=yaml.safe_load(args.config.read_text(encoding='utf-8-sig'))
    if args.rounds is not None:c['study']['rounds']=args.rounds
    if args.smoke:
        c['study'].update(rounds=2,broker_idx=0,display_transactions=80,fig1_bin_blocks=10,fig1_end_block=50)
        c['chain'].update(num_shards=2,num_funded_accounts=210)
        c['scale'].update(num_brokers=2,num_users=8)
        c['exp'].update(ctx_per_broker=40,rate=5,balances_eth=3,shift_to_shard=1,shift_cycle='1,0',pool_scan=2000,pool_scan_max=20000,drain_tail_s=240,max_backlog=1000,rss_cap_mb=4000)
        c['traffic'].update(value_cap_eth=1.0)
        c['broker']['initial_balance_eth']=3
    c['traffic']['real_csv_path']=str((root/c['traffic']['real_csv_path']).resolve())
    c['exp'].update(repetitions=c['study']['rounds'],profit_broker_idx=c['study']['broker_idx'],profit_tdr_cost_wei=c['study']['tdr_cost_wei'])
    tasks=plan(c);n=tasks[0]['n']
    print(json.dumps(dict(experiment=c['label'],revision=REVISION,smoke=args.smoke,rounds=c['study']['rounds'],tasks=len(tasks),user_ctx_per_arm=n,total_user_ctx=n*len(tasks),parameters=dict(epsilon=c['exp']['tdr_epsilon'],half_life=c['exp']['tdr_ewma_half_life'],q_min=c['exp']['tdr_q_min']),profit_x_axis='global completed-user-transaction index, directly recorded at TDR burn confirmation'),indent=2),flush=True)
    if args.dry_run:return 0
    # Preparing/importing an isolated source snapshot does not start chains and
    # remains safe while a different study owns the execution lock.
    lock=None
    if not args.prepare_only:
        lock=socket.socket()
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):lock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:lock.bind(('127.0.0.1',c['study']['lock_port']))
        except OSError as e:raise RuntimeError('Another exp040/041/042 runner or process occupies the shared lock port; run serially. No process was stopped.') from e
    args.output_root.mkdir(parents=True,exist_ok=True)
    run=args.resume.resolve() if args.resume else (args.output_root/datetime.now().strftime('%Y%m%d_%H%M%S')).resolve()
    if args.resume:
        m=json.loads((run/'manifest.json').read_text())
        if m['revision']!=REVISION or m['config']!=c or m['source_hashes']!=code_hashes():raise ValueError('Resume config/source changed; use a new run')
        tasks=m['tasks']
        for rel,h in m['backend_hashes'].items():
            if sha(run/'backend'/rel)!=h:raise ValueError('Frozen backend changed')
        assert sha(run/'workload.csv')==m['workload_sha256']
    else:
        run.mkdir(parents=True);frozen=stage(root,run/'backend',HERE)
        m=dict(revision=REVISION,config=c,smoke=args.smoke,formal=not args.smoke and c['study']['rounds']==10,source_hashes=code_hashes(),backend_hashes=frozen,tasks=tasks)
    sys.path.insert(0,str(run/'backend'))
    pipeline=importlib.import_module('experiments.exp025_regime_shift_sensitivity.pipeline')
    config=importlib.import_module('brokerlab.config')
    from brokerlab.procmon import RSSWatchdog
    def cfg_object(d,path):
        d=copy.deepcopy(d);d.pop('study');path.write_text(yaml.safe_dump(d,sort_keys=False),encoding='utf-8')
        return config.load_config(path)
    if not args.resume:
        historical=root/c['study']['historical_workload']
        if not args.smoke and not args.fresh_workload:
            if not historical.exists():raise FileNotFoundError('Frozen exp026 workload missing; pass --fresh-workload to generate a NEW fixed workload (not the historical input)')
            workload=read(historical);m['workload_origin']=str(historical)
        else:
            selection=cfg_object(c,run/'selection.yaml');workload,_=pipeline.build_workload(selection,run);m['workload_origin']='newly generated fixed workload'
        for r in workload:
            for k in ['src_shard','dst_shard','amount_wei','sender_idx','receiver_idx','arrival_pos']:r[k]=int(r[k])
        if len(workload)!=n or [r['arrival_pos'] for r in workload]!=list(range(n)):raise ValueError('Frozen workload length/order differs from design')
        write(run/'workload.csv',workload);m['workload_sha256']=sha(run/'workload.csv')
        save(run/'config.json',c);save(run/'manifest.json',m)
    workload=read(run/'workload.csv')
    for r in workload:
        for k in ['src_shard','dst_shard','amount_wei','sender_idx','receiver_idx','arrival_pos']:r[k]=int(r[k])
    print('RUN_DIRECTORY='+str(run),flush=True)
    if args.prepare_only:return 0
    def collect():
        results=[]
        for task in tasks:
            if task['status']=='complete':
                path=run/task['attempts'][-1]['path'];doc=json.loads((path/'result.json').read_text())
                for rel,h in doc['artifacts'].items():
                    if sha(path/rel)!=h:raise ValueError('Completed artifacts changed: '+str(path/rel))
                results.append({k:v for k,v in doc.items() if k!='artifacts'})
        if results:write(run/'results.csv',results)
        save(run/'report.json',dict(experiment=c['label'],revision=REVISION,smoke=args.smoke,complete=len(results)==len(tasks),completed=len(results),planned=len(tasks),paired_rounds=sum(all(t['status']=='complete' for t in tasks if t['round']==r) for r in range(1,c['study']['rounds']+1)),workload_sha256=m['workload_sha256'],profit_axis='recorded global user completion index',tdr_cost_basis='fixed nominal relay fee per confirmed burn; no actual gas debit'))
    try:
        for number,t in enumerate(tasks,1):
            if code_hashes()!=m['source_hashes']:raise RuntimeError('Experiment source changed during run')
            if t['status']=='complete':continue
            a=run/'tasks'/t['id']/f'a{len(t["attempts"])+1:03d}';a.mkdir(parents=True)
            attempt=dict(path=str(a.relative_to(run)),status='running');t['attempts'].append(attempt);t['status']='running';save(run/'manifest.json',m)
            print(f'[{number}/{len(tasks)}] {t["id"]} seed={t["route_seed"]}',flush=True)
            d=copy.deepcopy(c);d['exp'].update(route_seed=t['route_seed'],arms=t['arm']);d['tdr']['enabled']=t['arm']=='topup'
            cfg=cfg_object(d,a/'effective.yaml');cfg=replace(cfg,chain=replace(cfg.chain,base_port=free_ports(cfg.chain.base_port,cfg.chain.num_shards)))
            w=RSSWatchdog(float(cfg.exp['rss_cap_mb']));w.start()
            try:
                stats=pipeline.run_arm(cfg,t['arm'],workload,sum(r['amount_wei'] for r in workload),a,w,engine=t['arm'],fund_eth=c['exp']['balances_eth'],eps=c['exp']['tdr_epsilon'],chi=c['exp']['tdr_chi_blocks'],hl=c['exp']['tdr_ewma_half_life'],qmin=c['exp']['tdr_q_min'])
            finally:w.stop()
            save(a/'summary.json',dict(stats=stats,route_seed=t['route_seed'],effective=config.to_params_dict(cfg)))
            result=audit_task(c,t,a/t['arm'],stats,workload,a);result['artifacts']['summary.json']=sha(a/'summary.json')
            save(a/'result.json',result);t['status']=attempt['status']='complete';save(run/'manifest.json',m);collect()
    except BaseException:
        if 't' in locals() and t['status']=='running':t['status']='failed';t['attempts'][-1]['status']='failed'
        save(run/'manifest.json',m);collect();raise
    collect()
    if not args.no_plot:subprocess.run([sys.executable,str(HERE/'plot.py'),'--run',str(run)],check=True)
    print('COMPLETE '+str(run),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
