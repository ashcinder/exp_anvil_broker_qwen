"""Non-chain checks for the reference-style exp019 plot selection."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matplotlib.axes import Axes
from matplotlib.figure import Figure
import yaml

from brokerlab.config import load_config
from experiments.exp010_tdr_parameter_search.run import (
    build_trials, common_overrides, overrides_for_role,
)
from experiments.exp019_clean_ewma_parameter_search import plot_sensitivity as plot


class ReferenceStyleTest(unittest.TestCase):
    def test_search_plan_has_77_settings_and_ten_repeats(self):
        root = Path(__file__).resolve().parent
        space = yaml.safe_load((root / "search_space.yaml").read_text(encoding="utf-8"))
        trials = build_trials(
            load_config(root / "config.yaml"), space, "search", "oat",
            {"epsilon", "ewma_window_blocks", "ewma_half_life"})
        self.assertEqual(space["profiles"]["search"]["replicates"], 10)
        self.assertEqual(len(trials), 77)
        self.assertEqual(sum(view["kind"] == "oat"
                             for trial in trials for view in trial.views), 79)
        self.assertFalse(any(view["name"] == "epsilon" and view["x_value"] == 1.0
                             for trial in trials for view in trial.views))
        window_50 = next(trial for trial in trials if any(
            view["name"] == "ewma_window_blocks" and view["x_value"] == 50
            for view in trial.views))
        overrides = common_overrides(window_50, space["profiles"]["search"], 7)
        self.assertEqual(overrides["exp.tdr_ewma_window_blocks"], 50)
        self.assertNotIn("exp.tdr_ewma_window_blocks",
                         overrides_for_role(overrides, "plain"))

    def setUp(self):
        self.baseline = {
            "ewma_epsilon": 0.95,
            "ewma_window_blocks": 20,
            "ewma_half_life": 20,
        }
        self.expected = {
            "epsilon": {0.9, 0.95},
            "ewma_window_blocks": {20.0, 25.0},
            "ewma_half_life": {20.0, 25.0},
        }
        self.observed = {
            name: {x: {"target_transfers": "100", "target_relay": "20"}
                   for x in xs}
            for name, xs in self.expected.items()
        }

    def _samples(self, count):
        return {
            name: {x: {"transfers": list(range(100, 100 + count)),
                       "relay": list(range(20, 20 + count))}
                   for x in xs}
            for name, xs in self.expected.items()
        }

    def test_ten_replicates_use_dual_grouped_boxplots(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Figure, "savefig"), \
                patch.object(Axes, "boxplot") as boxplot:
            outputs = plot.draw_reference_style(
                Path(tmp), self.baseline, self.expected, self.observed,
                self._samples(10), 10)
        self.assertEqual(len(outputs), 6)  # Three figures, PNG and PDF.
        self.assertEqual(boxplot.call_count, 6)  # Two y-axes per figure.

    def test_single_replicate_never_uses_boxplots(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Figure, "savefig"), \
                patch.object(Axes, "boxplot") as boxplot:
            plot.draw_reference_style(
                Path(tmp), self.baseline, self.expected, self.observed,
                self._samples(1), 1)
        boxplot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
