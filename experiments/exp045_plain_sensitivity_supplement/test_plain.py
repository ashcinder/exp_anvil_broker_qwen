"""Regression checks for historical matching, zero-cost accounting and frozen evidence."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as exp


class PlainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, cls.bc = exp.load_base(exp.DEFAULT_B, 'b')
        cls.c, cls.cc = exp.load_base(exp.DEFAULT_C, 'c')

    def test_exact_cells_and_seeds(self):
        tasks = exp.build_tasks({'b': self.b, 'c': self.c})
        self.assertEqual(len(tasks), 180)
        self.assertEqual(len({t['id'] for t in tasks}), 180)
        self.assertEqual(sum(t['sweep'] == 'c' for t in tasks), 100)
        self.assertEqual(sum(t['sweep'] == 'b' for t in tasks), 80)
        self.assertEqual({t['route_seed'] for t in tasks}, set(range(7, 98, 10)))
        self.assertEqual({t['policy'] for t in tasks}, {'plain'})
        # Equal numerical settings belong to distinct historical runtimes.
        overlap = [t for t in tasks if t['balance_eth'] == 45 and t['transactions'] == 40000]
        self.assertEqual(len(overlap), 20)

    def test_disable_only_rebalancing(self):
        t = exp.build_tasks({'b': self.b, 'c': self.c})[0]
        source = self.bc if t['sweep'] == 'b' else self.cc
        original = copy.deepcopy(source[t['source_task_id']])
        config = exp.plain_config(original, t, dict(base_port=38600, anvil_bin='anvil.exe', smoke=False))
        self.assertFalse(config['tdr']['enabled'])
        self.assertEqual(config['exp']['arms'], 'plain')
        self.assertEqual(config['scale'], original['scale'])
        self.assertEqual(config['b2e'], original['b2e'])
        self.assertEqual(config['exp']['rate'], 120)
        self.assertEqual(config['exp']['sender_window'], original['exp']['sender_window'])
        self.assertTrue(original['tdr']['enabled'])

    def test_reuse_rejects_rate_fee_and_seed_changes(self):
        source = next(iter(self.bc.values()))
        t = next(t for t in exp.build_tasks({'b': self.b, 'c': self.c}) if t['sweep'] == 'b')
        config = exp.plain_config(source, t, dict(base_port=38600, anvil_bin='anvil.exe', smoke=False))
        for section, key, value in [('exp', 'rate', 121), ('exp', 'route_seed', 999), ('b2e', 'fee_share', .2), ('tdr', 'enabled', True)]:
            changed = copy.deepcopy(config)
            changed[section][key] = value
            with self.assertRaises(ValueError):
                exp.compatible_plain(config, changed)
        changed = copy.deepcopy(config)
        changed['chain']['base_port'] = 40000
        changed['label'] = 'administrative name'
        exp.compatible_plain(config, changed)

    def test_frozen_input_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'input.csv'
            p.write_text('original', encoding='utf-8')
            hashes = {'input.csv': exp.sha(p)}
            p.write_text('modified', encoding='utf-8')
            with self.assertRaises(ValueError):
                exp.check(Path(d), hashes)

    def test_actual_plain_raw_accounting_in_both_runtimes(self):
        folder = exp.DEFAULT_REUSE
        old = exp.read(folder / 'manifest.json')
        t = old['tasks'][0]
        attempt = folder / t['attempts'][-1]['path']
        doc = exp.read(attempt / 'summary.json')
        data = exp.rows(folder / 'w/40000.csv')
        raw = exp.rows(attempt / 'plain/ctx_rows.csv')
        for r in data:
            for key in ('amount_wei', 'arrival_pos', 'src_shard', 'dst_shard'):
                r[key] = int(r[key])
        for sweep in ('b', 'c'):
            spec = importlib.util.spec_from_file_location('accounting_' + sweep, HERE / 'runtime' / sweep / 'exp040_accounting.py')
            accounting = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(accounting)
            finals, _ = accounting.verify_and_account(doc['effective_config'], t, doc['stats'], raw, [], data)
            self.assertEqual(sum(int(r['assigned']) for r in finals), 40000)
            self.assertTrue(all(int(r['modeled_cost_wei']) == 0 for r in finals))
            self.assertTrue(all(int(r['net_profit_wei']) == int(r['served']) * 2100000 for r in finals))
            bad = copy.deepcopy(doc['stats'])
            bad['retained_fee_income_wei'] = int(bad['retained_fee_income_wei']) + 1
            with self.assertRaises(ValueError):
                accounting.verify_and_account(doc['effective_config'], t, bad, raw, [], data)

    def test_smoke_keeps_original_topology_and_separate_cells(self):
        tasks = exp.build_tasks({'b': self.b, 'c': self.c}, smoke=True)
        self.assertEqual(len(tasks), 2)
        self.assertEqual({t['transactions'] for t in tasks}, {2000})
        self.assertEqual({t['sweep'] for t in tasks}, {'b', 'c'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
