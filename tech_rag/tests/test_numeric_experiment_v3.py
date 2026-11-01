"""Synthetic information-dependence ablations, not market backtests."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class NumericExperimentTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('integration.numeric_experiment'), 'numeric experiment missing')
        from integration import numeric_experiment
        return numeric_experiment

    def test_fixed_seed_produces_four_explicit_synthetic_ablations(self):
        m = self.api()
        result = m.run_numeric_experiment(seed=17, n_events=32)
        self.assertEqual(result, m.run_numeric_experiment(seed=17, n_events=32))
        self.assertEqual(set(result['ablations']), {'factors_only', 'news_only', 'both_naive', 'rag_gate'})
        self.assertTrue(result['synthetic_only'])
        self.assertFalse(result['real_market_returns_used'])
        self.assertFalse(result['profitability_claimed'])
        json.dumps(result)

    def test_reprints_do_not_add_independent_event_confidence(self):
        m = self.api()
        result = m.run_numeric_experiment(seed=1, n_events=24, reprints_per_event=5)
        self.assertEqual(result['independent_event_count'], 24)
        self.assertEqual(result['news_observation_count'], 144)
        self.assertEqual(result['ablations']['rag_gate']['independent_event_count'], 24)
        self.assertGreater(result['ablations']['both_naive']['nominal_information_units'],
                           result['ablations']['rag_gate']['nominal_information_units'])
        self.assertGreater(result['possible_dependence_count'], 0)

    def test_gate_marks_ungrounded_numeric_proposals_without_inventing_returns(self):
        m = self.api()
        result = m.run_numeric_experiment(seed=19, n_events=40)
        gate = result['ablations']['rag_gate']
        self.assertGreater(gate['needs_more_evidence_count'], 0)
        self.assertGreater(gate['accepted_conditional_count'], 0)
        self.assertEqual(gate['execution_allowed'], False)
        self.assertIn('synthetic_prediction_mse', gate)
        self.assertNotIn('annual_return', gate)

    def test_artifact_roundtrips_and_invalid_budgets_fail_closed(self):
        m = self.api()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'numeric.json'
            report = m.save_numeric_artifact(path, seed=8, n_events=16)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), report)
        for kwargs in [dict(n_events=0), dict(seed=True), dict(reprints_per_event=-1), dict(n_events=1000001)]:
            with self.assertRaises(ValueError):
                m.run_numeric_experiment(**kwargs)


if __name__ == '__main__':
    unittest.main()
