import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class EvaluateV3Tests(unittest.TestCase):
    def test_interval_union_needs_full_span_and_does_not_double_count(self):
        from evaluate_v3 import interval_coverage
        self.assertEqual(interval_coverage(10, 20, [(5, 14), (12, 18), (18, 25)]), 1.0)
        self.assertEqual(interval_coverage(10, 20, [(10, 13), (10, 13)]), .3)
        self.assertEqual(interval_coverage(10, 20, [(20, 30)]), 0.0)

    def test_immutable_question_sets_have_expected_counts(self):
        from evaluate_v3 import frozen_questions
        sets = frozen_questions()
        self.assertEqual([len(sets[k]) for k in ('old24', 'holdout14')], [24, 14])
        self.assertEqual(sum(len(q['expected_evidence']) for q in sets['holdout14']), 32)

    def test_hash_mismatch_is_refused_before_evaluation(self):
        from evaluate_v3 import verify_digest
        with self.assertRaises(RuntimeError):
            verify_digest(Path(__file__), '0' * 64)

    def test_empty_relation_denominator_is_explicit(self):
        from evaluate_v3 import summarize_rows
        row = dict(expected_anchors=1, full_anchor_hits=0, any_overlap_hits=0,
                   citations_checked=0, citation_errors=[], relation_errors=[],
                   historical_leaks=[], expected_relations=0, gold_relation_hits=0,
                   expected_status='insufficient', unknown_case_correctly_abstained=False,
                   insufficient_reported=False, truncated=True, seconds=0.1,
                   text_chars=100, retrieval_tool_calls=2, budget_violations=[])
        result = summarize_rows([row])
        self.assertIsNone(result['gold_relation_recall'])
        self.assertEqual(result['known_unknown_abstentions'], 0)
        self.assertEqual(result['known_unknown_cases'], 1)
        self.assertEqual(result['full_anchor_recall'], 0.)

    def test_budget_overrun_is_failure_and_is_not_hidden_by_truncation(self):
        from evaluate_v3 import budget_checks
        packet = {'budget': {'max_total_chars': 700, 'max_tool_calls': 2},
                  'budget_usage': {'text_chars': 701, 'retrieval_tool_calls': 3}}
        self.assertEqual(set(budget_checks(packet)), {'text_chars', 'retrieval_tool_calls'})

    def test_missing_packet_in_comparison_cannot_be_silently_zipped_away(self):
        from evaluate_v3 import require_complete_batch
        with self.assertRaises(RuntimeError):
            require_complete_batch([{}, {}], [{}])
        require_complete_batch([{}], [{}])

    def test_frozen_holdout_audit_requires_raw_time_and_unreviewed_coverage(self):
        from evaluate_v3 import audit_annotations, frozen_questions
        from research import ResearchEngine
        engine = ResearchEngine(index_path=':memory:')
        audit = audit_annotations(frozen_questions()['holdout14'], engine)
        self.assertEqual(audit['anchors_checked'], 32)
        self.assertEqual(audit['invalid_anchors'], [])
        self.assertGreaterEqual(len(audit['fully_uncarded_questions']), 3)
        self.assertTrue(all(x['historical_eligible_at_cutoff'] for x in audit['anchors']
                            if x['historical']))


if __name__ == '__main__':
    unittest.main()
