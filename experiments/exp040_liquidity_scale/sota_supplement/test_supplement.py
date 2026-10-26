"""Integrity, paired design, real reference accounting and policy regression checks."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as supplement
sys.path.insert(0, str(HERE / 'runtime'))
from brokerlab.tdr_engine import TdrAgent
from brokerlab.sota_native import autoloop_plans
from exp040_accounting import verify_and_account


class SupplementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = supplement.DEFAULT_BASE
        if cls.base.exists():
            cls.manifest, cls.config, cls.configs = supplement.load_base(cls.base)

    def require_base(self):
        if not self.base.exists():
            self.skipTest('Original run not installed at default location')

    def test_only_two_new_methods_are_schedulable(self):
        self.require_base()
        tasks = supplement.build_tasks(self.manifest['tasks'], ['autoloop', 'spider'])
        self.assertEqual(len(tasks), 200)
        self.assertEqual(sum(t['transactions'] for t in tasks), 8000000)
        self.assertEqual({t['policy'] for t in tasks}, {'autoloop', 'spider'})
        self.assertEqual({t['route_seed'] for t in tasks}, set(range(7, 98, 10)))
        for t in self.manifest['tasks']:
            arms = [a for a in tasks if a['source_task_id'] == t['id']]
            self.assertEqual(len(arms), 2)
            self.assertTrue(all(a['balance_eth'] == t['balance_eth'] and a['route_seed'] == t['route_seed'] for a in arms))
        for forbidden in ['plain', 'topup', 'loopmax']:
            with self.assertRaises(ValueError):
                supplement.build_tasks(self.manifest['tasks'], [forbidden])

    def test_selector_keeps_original_round_seeds(self):
        self.require_base()
        tasks = supplement.build_tasks(self.manifest['tasks'], ['spider'], [45., 1000.], 2)
        self.assertEqual(len(tasks), 4)
        self.assertEqual({t['route_seed'] for t in tasks}, {7, 17})
        with self.assertRaises(ValueError):
            supplement.build_tasks(self.manifest['tasks'], ['spider'], [999.])

    def test_frozen_runtime_detects_mutation(self):
        p = HERE / 'runtime'
        supplement.verify_hashes(p, supplement.read_json(p / 'provenance.json')['hashes'])
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / 'x.py'
            q.write_text('original')
            h = {'x.py': supplement.sha(q)}
            q.write_text('changed')
            with self.assertRaises(ValueError):
                supplement.verify_hashes(Path(d), h)

    def test_autoloop_only_moves_outside_band_accounts(self):
        self.assertEqual(autoloop_plans([10, 100, 190], 200, .3, .7), [(2, 0, 90)])
        self.assertEqual(autoloop_plans([60, 100, 140], 200, .3, .7), [])
        # An account inside the band is not drained to fund a different account.
        self.assertEqual(autoloop_plans([10, 100, 110], 200, .3, .7), [])

    def test_autoloop_uses_two_B_capacity_and_10_block_checks(self):
        a = TdrAgent(3, policy='autoloop', epsilon=.95, chi_blocks=0,
                     valve_init_wei=100, sota_capacity_wei=200, sota_check_blocks=10)
        self.assertEqual(a.tick(0, [10, 100, 190]), [(2, 0, 90)])
        a.state, a.active = a.ST_IDLE, []
        self.assertEqual(a.tick(9, [10, 100, 190]), [])
        self.assertEqual(a.tick(10, [10, 100, 190]), [(2, 0, 90)])

    def test_spider_counts_successful_principal_not_submitted_demand(self):
        a = TdrAgent(2, policy='spider_vol', epsilon=.95, chi_blocks=0, spider_vol_wei=100)
        a.observe(0, 1, 1000)
        self.assertEqual(a.tick(0, [90, 10]), [])
        a.record_success(99)
        self.assertEqual(a.tick(1, [90, 10]), [])
        a.record_success(1)
        self.assertEqual(a.tick(2, [90, 10]), [(0, 1, 40)])

    def test_real_original_accounting_reproduces_saved_profit(self):
        self.require_base()
        t = self.manifest['tasks'][0]
        path = self.base / t['attempts'][-1]['path']
        doc = supplement.read_json(path / 'summary.json')
        finals, curve = verify_and_account(doc['effective_config'], t, doc['stats'],
            supplement.read_csv(path / 'topup/ctx_rows.csv'),
            supplement.read_csv(path / 'topup/tdr_moves.csv'), supplement.read_csv(self.base / 'w/40000.csv'))
        expected = supplement.read_csv(path / 'per_broker.csv')
        for actual, saved in zip(finals, expected):
            for key in ('assigned', 'served', 'relay', 'income_wei', 'upper_wei',
                        'tdr_transfers', 'modeled_cost_wei', 'net_profit_wei'):
                self.assertEqual(actual[key], int(saved[key]))
        self.assertEqual(sum(a['upper_wei'] for a in finals), 84000000000)
        bad_moves = supplement.read_csv(path / 'topup/tdr_moves.csv')[:-1]
        with self.assertRaises(ValueError):
            verify_and_account(doc['effective_config'], t, doc['stats'],
                supplement.read_csv(path / 'topup/ctx_rows.csv'), bad_moves,
                supplement.read_csv(self.base / 'w/40000.csv'))

    def test_partial_plot_never_zero_fills_missing_methods(self):
        import plot
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'reference').mkdir()
            tasks = []
            original, added = [], []
            for rd in (1, 2):
                meta = dict(task_id=f'base{rd}', round=rd, route_seed=7 + (rd - 1) * 10,
                    balance_eth=45, broker_idx=19, transactions=40000,
                    upper_wei=1700000000, net_profit_wei=1000000000)
                original.append(meta)
                for method in ('autoloop', 'spider'):
                    task = dict(id=f'{method}{rd}', source_task_id=f'base{rd}', policy=method,
                                status='pending' if rd == 2 and method == 'spider' else 'complete')
                    tasks.append(task)
                    added.append({**meta, 'task_id': task['id']})
            supplement.save(p / 'manifest.json', dict(smoke=False, tasks=tasks))
            supplement.write_csv(p / 'reference/per_broker.csv', original)
            supplement.write_csv(p / 'per_broker.csv', added)
            with self.assertRaises(ValueError):
                plot.build_observations(p)
            _, rows = plot.build_observations(p, allow_partial=True)
            self.assertEqual(len(rows), 4)
            self.assertEqual({r['round'] for r in rows}, {1})


if __name__ == '__main__':
    unittest.main(verbosity=2)
