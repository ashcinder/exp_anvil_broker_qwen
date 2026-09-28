"""Configuration/dispatch tests only: never launch an experiment or read CSV rows."""
import ast
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("exp011_entry_test", HERE / "run.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.cfg = entry.load_config(HERE / "config.yaml")

    def test_formal_plan_and_isolated_output(self):
        runner = entry.load_exp008()
        plan = entry.check_plan(self.cfg, runner)
        self.assertEqual(plan["arm_runs"], 10)
        self.assertEqual(plan["ctx_per_arm"], 40000)
        self.assertEqual(plan["route_seeds"], [7, 17])
        self.assertEqual(Path(plan["output_root"]), HERE / "out")
        self.assertEqual(runner.EXP003_RUN, ROOT / "experiments/exp003_tdr_on_off/run.py")
        self.assertEqual(Path(plan["trace"]), ROOT / "trace/ETH_cleaned.csv")
        self.assertFalse(self.cfg.traffic.allow_contract_endpoints)
        self.assertIn(self.cfg.exp["exp008_final_arm"], plan["arms"])

    def test_teacher_f1_parameters_preserved(self):
        teacher = entry.load_config(ROOT / "experiments/exp010_param_grid/exp010_param_grid/config.yaml")
        self.assertEqual(self.cfg.exp["arms"].split(","), teacher.exp["batches"]["F1_main_table_refresh"]["arms"])
        changed = {"ctx_per_broker", "arms"}
        for key in set(self.cfg.exp) & set(teacher.exp) - changed:
            self.assertEqual(self.cfg.exp[key], teacher.exp[key], key)
        self.assertNotIn("tdr_surplus_epsilon", self.cfg.exp)
        self.assertEqual(self.cfg.exp["tdr_target_cap"], 0)
        self.assertEqual(self.cfg.exp["tdr_min_reserve_eth"], 0)

    def test_current_pipeline_required_keys(self):
        tree = ast.parse((ROOT / "experiments/exp003_tdr_on_off/run.py").read_text(encoding="utf-8"))
        keys = {node.args[1].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "exp_param" and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)}
        keys.update(["max_inflight", "poll_s", "probe_s", "probe_first_s",
                     "relay_timeout_s", "engine_timeout_s"])
        self.assertFalse(keys - self.cfg.exp.keys(), keys - self.cfg.exp.keys())

    def test_check_never_calls_experiment(self):
        runner = entry.load_exp008()
        with patch.object(entry, "load_exp008", return_value=runner), \
                patch.object(runner, "main") as run, \
                patch.object(entry.sys, "argv", [str(HERE / "run.py"), "--check"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(), 0)
            run.assert_not_called()

    def test_normal_entry_delegates_and_preserves_cli(self):
        runner = entry.load_exp008()
        args = [str(HERE / "run.py"), "--set", "exp.exp008_sessions=1"]
        with patch.object(entry, "load_exp008", return_value=runner), \
                patch.object(runner, "main", return_value=23) as run, \
                patch.object(entry.sys, "argv", args), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(), 23)
            self.assertEqual(entry.sys.argv, args)
            run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
