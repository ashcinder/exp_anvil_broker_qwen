import csv,copy,multiprocessing as mp,tempfile,unittest
from pathlib import Path
import yaml
from design import plan
from staging import once
from accounting_logger import install

class AccountingTests(unittest.TestCase):
    def test_plan_is_ten_paired_rounds_not_parameter_scan(self):
        c=yaml.safe_load((Path(__file__).parent/'config.yaml').read_text());ts=plan(c)
        self.assertEqual(len(ts),20)
        self.assertEqual({t['n'] for t in ts},{80000})
        for r in range(1,11):
            pair=[t for t in ts if t['round']==r]
            self.assertEqual({t['arm'] for t in pair},{'plain','topup'})
            self.assertEqual({t['route_seed'] for t in pair},{7+(r-1)*10})
        self.assertEqual(ts[0]['arm'],'plain');self.assertEqual(ts[2]['arm'],'topup')
    def test_shared_completion_index_and_tdr_fee_event(self):
        class Engine:
            def __init__(self):self.rows=[]
            def _emit(self,r):self.rows.append(r)
        with tempfile.TemporaryDirectory() as tmp:
            counter=mp.get_context('spawn').Value('q',0);a,b=Engine(),Engine()
            install(a,counter,tmp,0,21000000);install(b,counter,tmp,1,21000000)
            a._emit({'ctx_id':'a'});b._emit({'ctx_id':'b'})
            event=dict(h1='0xabc',src=0,dst=1,amount=123,burn_block=17)
            a._exp042_record_cost(event);b._emit({'ctx_id':'c'})
            self.assertEqual([a.rows[0]['accounting_index'],b.rows[0]['accounting_index'],b.rows[1]['accounting_index']],[1,2,3])
            with (Path(tmp)/'broker_0_costs.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(int(rows[0]['transaction_index']),2)
            self.assertEqual(int(rows[0]['cost_wei']),21000000)
            with self.assertRaises(RuntimeError):a._exp042_record_cost(event)
    def test_unknown_backend_patch_fails_closed(self):
        with self.assertRaises(RuntimeError):once('not compatible','anchor','replacement')

if __name__=='__main__':unittest.main()
