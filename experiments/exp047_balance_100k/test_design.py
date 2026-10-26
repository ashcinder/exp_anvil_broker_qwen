import unittest,copy
import run

class MatchedDesign(unittest.TestCase):
    def test_formal_pairing(self):
        tasks=run.plan();self.assertEqual(len(tasks),280)
        self.assertEqual(len({t['id'] for t in tasks}),280)
        for rep in range(1,11):
            for b in run.BALANCES:
                cell=[t for t in tasks if t['round']==rep and t['balance_eth']==b]
                self.assertEqual({t['policy'] for t in cell},set(run.METHODS))
                self.assertEqual({t['route_seed'] for t in cell},{7+(rep-1)*10})
                self.assertEqual({t['transactions'] for t in cell},{100000})
    def test_config_only_policy_changes_within_cell(self):
        base=run.read(run.BASE/'config.json');original=copy.deepcopy(base)
        m={'base_port':42600,'anvil_bin':'anvil'}
        configs=[run.effective(base,t,m) for t in run.plan()[:4]]
        self.assertEqual(base,original)
        normalized=[]
        for c in configs:
            self.assertEqual(c['exp']['ctx_per_broker']*50,100000)
            self.assertEqual(c['b2e'],base['b2e'])
            self.assertEqual(c['exp']['tdr_ewma_min_blocks'],5)
            c['exp'].pop('arms');c['tdr'].pop('enabled');normalized.append(c)
        self.assertTrue(all(c==normalized[0] for c in normalized))
    def test_smoke_separate(self):
        tasks=run.plan(True)
        self.assertEqual(len(tasks),4);self.assertEqual({t['transactions'] for t in tasks},{2000})
        self.assertTrue({t['id'] for t in tasks}.isdisjoint({t['id'] for t in run.plan()}))

if __name__=='__main__':unittest.main()
