"""Fast design/report checks for exp021; never launches Anvil."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brokerlab.config import load_config, to_params_dict
from experiments.exp021_tdr_ablation import run


class Exp021AblationTests(unittest.TestCase):
    def test_csv_counts_only_completed_tdr_transfers(self):
        with tempfile.TemporaryDirectory() as temp:
            arm_dir = Path(temp)
            (arm_dir / "ctx_rows.csv").write_text(
                "ok,route\nTrue,relay\nTrue,broker\nTrue,relay\n",
                encoding="utf-8")
            (arm_dir / "tdr_moves.csv").write_text(
                "state\ndone\nlost\ndone\n", encoding="utf-8")
            self.assertEqual(run._csv_counts(arm_dir), (3, 2, 2))

    def test_same_exp008_settings_except_ablation_design(self):
        old = to_params_dict(load_config(
            run.HERE.parent / "exp008_tdr_schedule_final" / "config.yaml"))
        new_cfg = load_config(run.HERE / "config.yaml")
        new = to_params_dict(new_cfg)
        arms = run.validate_design(new_cfg)
        self.assertEqual(len(arms), 5)
        self.assertEqual({a["epsilon"] for a in arms if a["policy"]}, {0.95})
        self.assertEqual({a["ewma_half_life_blocks"] for a in arms if a["policy"]},
                         {0.0, 20.0})
        self.assertEqual({a["policy"] for a in arms if a["policy"]},
                         {"proportional", "topup"})
        self.assertEqual({k: v for k, v in old.items() if k not in {"label", "exp"}},
                         {k: v for k, v in new.items() if k not in {"label", "exp"}})
        allowed = {"arms", "exp008_ctx_total", "exp008_final_arm",
                   "exp008_max_throughput_loss", "exp008_min_relay_reduction",
                   "exp008_sessions", "exp021_ctx_total", "tdr_epsilon"}
        self.assertEqual({k: v for k, v in old["exp"].items() if k not in allowed},
                         {k: v for k, v in new["exp"].items() if k not in allowed})

    def test_score_uses_completed_transfers_not_events(self):
        cfg = load_config(run.HERE / "config.yaml")
        arms = run.validate_design(cfg)
        counts = {
            "plain": (80000, 51850, 0),
            "prop_hard": (80000, 30, 100),
            "prop_ewma": (80000, 25, 90),
            "topup_hard": (80000, 20, 80),
            "full": (80000, 10, 70),
        }
        by_tag = {a["tag"]: a for a in arms}
        summary = {
            "passed": True,
            "params": {"_runtime": {"total_ctx": 80000}},
            "arms": [{"arm": a["tag"], "n": 80000,
                      "relayed": counts[a["key"]][1], "failed": 0,
                      "coord": {"aborted": None}, "gates": {"G1": True},
                      "burn_reconcile": {"ok": True},
                      "tdr": {"transfers_done": counts[a["key"]][2],
                              "events_opened": 999, "moved_wei": 0},
                      "throughput_ctx_per_s": 62.0} for a in arms],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            with patch.object(run, "_csv_counts",
                              side_effect=lambda path: counts[by_tag[path.name]["key"]]):
                report = run.summarize(root, arms)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["arms"][-1]["relay_plus_tdr_transfers"], 80)
        self.assertEqual(report["comparisons"]["remove_topup_keep_ewma"], 35)
        self.assertEqual(report["comparisons"]["remove_ewma_keep_topup"], 20)


if __name__ == "__main__":
    unittest.main()
