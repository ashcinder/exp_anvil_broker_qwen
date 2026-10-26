"""Synthetic software fixtures, NOT scientific experimental evidence."""
import copy,json,multiprocessing as mp,subprocess,sys,tempfile,unittest,os,shutil
from pathlib import Path
import yaml
from design import audit_task,write,save,plan
from accounting_logger import install

def spawn_logger(counter,directory,broker,q):
    class Engine:
        def _emit(self,row):q.put(row['accounting_index'])
    eng=Engine();install(eng,counter,directory,broker,21000000)
    for i in range(3):eng._emit({'ctx_id':f'{broker}-{i}'})

class IntegrationTests(unittest.TestCase):
    def test_spawned_workers_share_one_completion_order(self):
        context=mp.get_context('spawn');counter=context.Value('q',0);q=context.Queue()
        with tempfile.TemporaryDirectory() as tmp:
            ps=[context.Process(target=spawn_logger,args=(counter,tmp,b,q)) for b in range(2)]
            for p in ps:p.start()
            for p in ps:p.join(30);self.assertEqual(p.exitcode,0)
            self.assertEqual(sorted(q.get(timeout=5) for _ in range(6)),list(range(1,7)))
    def test_accounting_and_all_figures(self):
        home=Path(__file__).resolve().parent
        c=yaml.safe_load((home/'config.yaml').read_text());c['study'].update(rounds=2,broker_idx=0,display_transactions=40,fig1_end_block=50,fig1_bin_blocks=10)
        c['scale'].update(num_brokers=2,num_users=8);c['exp']['ctx_per_broker']=20
        tasks=plan(c);workload=[dict(ctx_id=f'fixture{i}',arrival_pos=i,src_shard=0,dst_shard=1,amount_wei=100) for i in range(40)]
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            for t in tasks:
                out=run/t['id'];arm=out/t['arm'];(arm/'accounting').mkdir(parents=True)
                rows=[]
                for i,w in enumerate(workload):
                    served=t['arm']=='topup' or i%(3 if t['round']==1 else 4)==0
                    rows.append(dict(ctx_id=w['ctx_id'],arrival_pos=i,src=0,dst=1,amount_wei=100,accounting_index=i+1,broker_idx=i%2,route='broker' if served else 'relay',ok=True,fee_broker_wei=2100000 if served else 0,submit_block=i+1))
                events=[]
                if t['arm']=='topup':
                    for j,k in enumerate([0,40] if t['round']==1 else [20]):
                        events.append(dict(broker_idx=0,transaction_index=k,observed_monotonic_ns=j,transfer_hash=f'tx{j}',src_shard=0,dst_shard=1,amount_wei=10,burn_block=9+j,cost_wei=21000000))
                # Empty cost files must keep their schema too.
                if events:write(arm/'accounting/broker_0_costs.csv',events)
                else:(arm/'accounting/broker_0_costs.csv').write_text('broker_idx,transaction_index,transfer_hash,cost_wei\n')
                move_rows=[dict(broker_idx=0,src_shard=0,dst_shard=1,amount_wei=10,burn_block=9+j,mint_block=12+j,state='done') for j in range(len(events))]
                if move_rows:write(arm/'tdr_moves.csv',move_rows)
                else:(arm/'tdr_moves.csv').write_text('broker_idx,src_shard,dst_shard,amount_wei,burn_block,mint_block,state\n')
                write(arm/'ctx_rows.csv',rows)
                stats=dict(gates={'ok':True},burn_reconcile={'ok':True},failed=0,tdr=dict(transfers_lost=0,lost_in_transit_wei=0,transfers_done=len(events)),served=sum(r['route']=='broker' for r in rows),relayed=sum(r['route']=='relay' for r in rows),retained_fee_income_wei=sum(r['fee_broker_wei'] for r in rows),wall_s=1)
                result=audit_task(c,t,arm,stats,workload,out)
                if t['arm']=='topup' and t['round']==1:
                    from design import read
                    curve=read(out/'curve.csv')
                    self.assertEqual(int(curve[0]['cost_wei']),21000000)
                    self.assertEqual(int(curve[-1]['cost_wei']),42000000)
                    self.assertEqual(int(curve[-1]['profit_wei']),0)
                    broken=copy.deepcopy(rows);broken[1]['accounting_index']=1;write(arm/'ctx_rows.csv',broken)
                    with self.assertRaises(AssertionError):audit_task(c,t,arm,stats,workload,out)
                    write(arm/'ctx_rows.csv',rows)
                save(out/'result.json',result);t['status']='complete';t['attempts']=[dict(path=t['id'])]
            save(run/'manifest.json',dict(config=c,tasks=tasks,smoke=True,synthetic_software_fixture=True))
            plotted=subprocess.run([sys.executable,str(home/'plot.py'),'--run',str(run)],capture_output=True,text=True)
            self.assertEqual(plotted.returncode,0,plotted.stdout+'\n'+plotted.stderr)
            for name in ['fig1','fig1a','fig1b','fig5a','fig5a_full']:
                self.assertTrue((run/'figs'/f'{name}.pdf').exists())
            d=json.loads((run/'figs/plot_metadata.json').read_text())
            self.assertEqual(d['n'],2);self.assertFalse(d['cost_position_is_projected'])
            if os.environ.get('EXP042_VALIDATION_OUTPUT'):
                destination=Path(os.environ['EXP042_VALIDATION_OUTPUT']).resolve()
                if destination.exists():raise RuntimeError('Validation destination already exists; use a new path')
                shutil.copytree(run,destination)

if __name__=='__main__':unittest.main()
