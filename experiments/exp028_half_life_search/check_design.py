"""Offline verification of pairing and metric selection; never starts Anvil."""
from pathlib import Path
import sys
import unittest
import pandas as pd

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from brokerlab.config import load_config,apply_overrides
from experiments.exp028_half_life_search.runner import make_tasks,parse_values,GRIDS
from experiments.exp028_half_life_search.analyze import summarize,decisions

class DesignChecks(unittest.TestCase):
    def test_task_pairing(self):
        cfg=apply_overrides(load_config(HERE/'config.yaml'),['exp.repetitions=6'])
        tasks,space=make_tasks(cfg,GRIDS['coarse'],2842)
        self.assertEqual(len(tasks),66)
        self.assertEqual(len({t['id'] for t in tasks}),66)
        self.assertEqual(tasks,make_tasks(cfg,GRIDS['coarse'],2842)[0])
        self.assertNotEqual(tasks,make_tasks(cfg,GRIDS['coarse'],123)[0])
        for rep in range(1,7):
            group=[t for t in tasks if t['round']==rep]
            self.assertEqual({t['value'] for t in group},set(GRIDS['coarse']))
            self.assertEqual({t['route_seed'] for t in group},{7+10*(rep-1)})
            for task in group:
                self.assertIn('@0.95@@',task['token'])
                self.assertTrue(task['token'].endswith('@0.3'))

    def test_reject_invalid_values(self):
        for text in ['4,4,20','0,4,20','nan,4,20','inf,4,20','4,,20']:
            with self.assertRaises(ValueError):parse_values(text)
        self.assertEqual(parse_values('1000,4,20'),[4,20,1000])

    def test_two_metrics_need_different_winners(self):
        # Explicit unit-test fixture; these are not measured experiment results.
        rows=[]
        for rep in range(1,7):
            for h,relay,total in [(4,10,100),(20,20,80),(100,40,80.5)]:
                rows.append(dict(half_life=h,round=rep,route_seed=7+10*(rep-1),relay=relay+rep,
                                 tdr=total-relay,combined=total+rep,relay_rate=0.01,
                                 throughput=60,submission_span_blocks=600))
        df=pd.DataFrame(rows);table=summarize(df);out,pairs=decisions(df,table,.01)
        self.assertEqual(out['empirical_best_h'],{'relay':4.0,'combined':20.0})
        self.assertEqual(out['within_tolerance_h'],[20,100])
        self.assertEqual(out['shortest_within_tolerance'],20)
        self.assertEqual(set(pairs.paired_repeats),{6})
        r=pairs[(pairs.metric=='combined')&(pairs.candidate_h==100)].iloc[0]
        self.assertAlmostEqual(r.mean_difference,.5)

if __name__=='__main__':unittest.main()
