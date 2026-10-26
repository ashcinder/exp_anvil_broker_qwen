import copy
import unittest
from pathlib import Path
import yaml
from design import METHODS,build_tasks,effective,validate,summarize

class DesignTests(unittest.TestCase):
    def setUp(self):self.c=yaml.safe_load((Path(__file__).parent/'config.yaml').read_text(encoding='utf-8'))
    def test_exact_formal_counts_and_paired_seeds(self):
        tasks=build_tasks(self.c)
        self.assertEqual(len(tasks),15);self.assertEqual(sum(t['transactions'] for t in tasks),1500000)
        for arm in METHODS:
            rr=[t for t in tasks if t['policy']==arm]
            self.assertEqual([t['route_seed'] for t in rr],[7,17,27]);self.assertTrue(all(t['transactions']==100000 for t in rr))
    def test_engine_aliases_never_fall_back_to_plain(self):
        tasks=build_tasks(self.c);lookup={t['policy']:t for t in tasks}
        self.assertEqual(lookup['prop_ewma']['engine'],'proportional')
        self.assertEqual(lookup['topup_e0']['engine'],'topup')
        d=effective(self.c,lookup['topup_e0'],40600,'anvil.exe')
        self.assertEqual(d['exp']['tdr_epsilon'],0);self.assertEqual(d['tdr']['epsilon'],0)
        self.assertEqual(self.c['exp']['tdr_epsilon'],.95)
        bad=copy.deepcopy(lookup['prop_ewma']);bad['engine']='plain'
        with self.assertRaises(ValueError):effective(self.c,bad,40600,'anvil.exe')
    def test_refuse_other_lengths_rounds_and_method_deletions(self):
        for key,value in [('transactions',80000),('repetitions',1),('methods',['autoloop','topup'])]:
            c=copy.deepcopy(self.c);c['exp'][key]=value
            with self.assertRaises(ValueError):validate(c)
    def test_nominal_fee_not_chain_default(self):
        c=copy.deepcopy(self.c);c['b2e']['ref_gas_price_wei']=1000000000
        with self.assertRaises(ValueError):validate(c)
    def test_system_billing_is_not_divided_by_brokers(self):
        c=copy.deepcopy(self.c);c['scale']['num_brokers']=2
        rows=[dict(broker_idx=0,route='broker',xmit_bytes=100),dict(broker_idx=1,route='relay',xmit_bytes=1800,t1_tx_bytes=100,t1_receipt_bytes=1700)]
        a=[dict(broker_idx=i,assigned=1,served=int(i==0),relay=int(i==1),tdr_transfers=0,income_wei=2100000 if i==0 else 0,modeled_cost_wei=0,net_profit_wei=2100000 if i==0 else 0) for i in range(2)]
        totals=summarize(c,rows,[],a)
        self.assertEqual(totals['user_ctx_communication_bytes'],1900)
        self.assertEqual(totals['profit_gwei'],.0021)
        self.assertEqual(a[1]['user_ctx_communication_bytes'],1800)
    def test_reject_fabricated_billing(self):
        with self.assertRaises(ValueError):summarize(self.c,[dict(broker_idx=0,route='broker',xmit_bytes=200)],[],[])
    def test_profit_axis_round_trip_and_outside_ticks(self):
        import numpy as np
        from plot import mapped,scale_for
        values=[[-20,-18],[-.5,.5],[.1,.7]]
        knots,screen,gap=scale_for(values)
        self.assertIsNotNone(gap)
        grid=np.array([-30,-20,-19,-18,-.5,0,.5,.7,10.])
        transformed=mapped(grid,knots,screen)
        np.testing.assert_allclose(mapped(transformed,screen,knots),grid,atol=1e-10)
        self.assertTrue(np.all(np.diff(transformed)>0))
        self.assertTrue(all(not (gap[0]<v<gap[1]) for g in values for v in g))

if __name__=='__main__':unittest.main()
