from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rung1_mechanism import DictView, ForkConnectome, run_heat_scout, make_permutation
from rung1_stats import spearman, holm_adjust

PLAN = json.loads((SCRIPT_DIR.parent / "EXPERIMENT_PLAN.json").read_text(encoding="utf-8"))


class PackageTests(unittest.TestCase):
    def test_native_heatscout_exact_replay_under_harness_seed(self):
        a = run_heat_scout(ForkConnectome(), 0, DictView({1: 1.0, 2: 0.0}), PLAN, 12345)
        b = run_heat_scout(ForkConnectome(), 0, DictView({1: 1.0, 2: 0.0}), PLAN, 12345)
        self.assertEqual(a["edges"], b["edges"])
        self.assertEqual(a["touches"], b["touches"])

    def test_heatscout_respects_declared_edge_budget(self):
        result = run_heat_scout(ForkConnectome(), 0, DictView({1: 0.0, 2: 0.0}), PLAN, 7)
        self.assertLessEqual(len(result["edges"]), PLAN["walker_budget"]["edges"])
        self.assertLessEqual(len(result["touches"]), PLAN["walker_budget"]["visits"])

    def test_large_heat_difference_controls_first_choice(self):
        for seed in range(20):
            result = run_heat_scout(ForkConnectome(), 0, DictView({1: 20.0, 2: 0.0}), PLAN, seed)
            self.assertTrue(result["edges"])
            self.assertEqual(result["edges"][0], (0, 1))

    def test_permutation_is_reproducible_and_bijective(self):
        a = make_permutation(50, 99)
        b = make_permutation(50, 99)
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), list(range(50)))

    def test_spearman_ties_and_order(self):
        self.assertAlmostEqual(spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3], [30, 20, 10]), -1.0)
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))

    def test_holm_adjustment_is_monotone_and_bounded(self):
        out = holm_adjust([("a", 0.001), ("b", 0.02), ("c", 0.04)])
        self.assertTrue(all(0.0 <= p <= 1.0 for p in out.values()))
        self.assertLessEqual(out["a"], out["b"])
        self.assertLessEqual(out["b"], out["c"])


if __name__ == "__main__":
    unittest.main()
