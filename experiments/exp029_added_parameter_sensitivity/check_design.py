"""Offline endpoint and process-spawn validation; no chains started."""
from pathlib import Path
import sys
import json
import random
import unittest
import multiprocessing as mp
import yaml

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from brokerlab.config import load_config,apply_overrides
from brokerlab.tdr_engine import TdrAgent as OriginalAgent
from experiments.exp029_added_parameter_sensitivity.tdr_engine import TdrAgent
from experiments.exp029_added_parameter_sensitivity.runner import make_tasks

def spawn_probe(q):
    a=TdrAgent(2,policy='topup',epsilon=1,surplus_epsilon=1,demand_ewma_half_life=20,demand_ewma_min_blocks=1)
    a.observe(1,1,1000)
    q.put((a.epsilon,a.tick(2,[1000,0]),a.last_target is not None))

class Checks(unittest.TestCase):
    def test_grid_and_pairing(self):
        cfg=apply_overrides(load_config(HERE/'config.yaml'),['exp.repetitions=10']);space=yaml.safe_load((HERE/'search_space.yaml').read_text())
        tasks=make_tasks(cfg,space)
        self.assertEqual(len(tasks),110);self.assertEqual(len({t['id'] for t in tasks}),110)
        for rep in range(1,11):
            g=[t for t in tasks if t['round']==rep]
            self.assertEqual(len(g),11);self.assertEqual({t['route_seed'] for t in g},{7+10*(rep-1)})
            self.assertEqual(sum(t['sweep']=='epsilon' for t in g),5)
            self.assertEqual(sum(t['sweep']=='q_min' for t in g),6)
            for t in g:
                state=t['effective_tdr']
                if t['sweep']=='epsilon':self.assertEqual((state['half_life'],state['q_min']),(20,.3))
                else:self.assertEqual((state['half_life'],state['epsilon']),(10,.95))

    def test_boundary_keeps_exact_one_and_never_triggers(self):
        rng=random.Random(29)
        for _ in range(100):
            a=TdrAgent(16,policy='topup',epsilon=1,surplus_epsilon=1,demand_ewma_half_life=20,demand_ewma_min_blocks=1)
            a.observe(1,rng.randrange(16),rng.randrange(1,100000))
            self.assertEqual(a.tick(2,[rng.randrange(0,100000) for _ in range(16)]),[])
            self.assertIsNotNone(a.last_target);self.assertEqual(a.epsilon,1)
            self.assertEqual(a.events_opened,0)
        with self.assertRaises(ValueError):TdrAgent(2,policy='topup',epsilon=1.01)
        with self.assertRaises(ValueError):TdrAgent(2,policy='topup',epsilon=1,topup_two_sided=True)
        with self.assertRaises(ValueError):OriginalAgent(2,policy='topup',epsilon=1)

    def test_interior_matches_original_agent(self):
        rng=random.Random(290)
        for epsilon in [.95,.96,.97,.98,.99]:
            old=OriginalAgent(16,policy='topup',epsilon=epsilon,q_min=.3,demand_ewma_half_life=20,demand_ewma_min_blocks=1)
            new=TdrAgent(16,policy='topup',epsilon=epsilon,q_min=.3,demand_ewma_half_life=20,demand_ewma_min_blocks=1)
            for b in range(1,20):
                shard=rng.randrange(16);amount=rng.randrange(1,100000)
                old.observe(b,shard,amount);new.observe(b,shard,amount)
            balances=[rng.randrange(0,100000) for _ in range(16)]
            self.assertEqual(old.tick(20,balances),new.tick(20,balances))
            self.assertEqual(old.last_target,new.last_target)

    def test_windows_spawn_uses_local_boundary_agent(self):
        ctx=mp.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=spawn_probe,args=(q,));p.start()
        self.assertEqual(q.get(timeout=20),(1.0,[],True));p.join(20)
        self.assertEqual(p.exitcode,0);q.close()

if __name__=='__main__':unittest.main()
