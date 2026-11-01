import sys, unittest
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retrieval_config import RetrievalConfig
from evaluation.rag_benchmark import draft_benchmark,validate_labels,metrics,paired_interval,fusion_grid,comparison_report

class BenchmarkTests(unittest.TestCase):
    def test_drafts_are_not_gold_and_group_splits(self):
        draft=draft_benchmark();rows=validate_labels(draft)
        self.assertEqual(Counter(r['ticker'] for r in rows),{'NVDA':40,'MU':40,'TSM':40})
        self.assertEqual(Counter(r['split'] for r in rows),{'development':60,'validation':20,'sealed_test':40})
        self.assertFalse(metrics(rows[0],[])['formal'])
        rows[1]['split']='validation'
        with self.assertRaises(ValueError):validate_labels(draft)
    def test_relevance_and_redundancy_are_separate(self):
        row=dict(review_status='reviewed',facts=[dict(fact_id='a'),dict(fact_id='b')],qrels={
            '1':dict(grade=3,fact_ids=['a'],duplicate_cluster='a'),
            '2':dict(grade=2,fact_ids=['a'],duplicate_cluster='a'),
            '3':dict(grade=0,fact_ids=[],duplicate_cluster='x')})
        m=metrics(row,['1','2','3']);self.assertEqual(m['fact_recall'],.5)
        self.assertEqual(m['duplicate_fact_ratio'],.5);self.assertAlmostEqual(m['evidence_precision'],2/3)
        self.assertFalse(metrics(row,['unlabelled'])['formal'])
    def test_parameters_validate_and_fingerprint(self):
        c=RetrievalConfig();self.assertEqual(c.weights('disclosure_detail')['keyword'],3)
        self.assertNotEqual(c.version,RetrievalConfig(rrf_k=30).version)
        self.assertEqual(len(list(fusion_grid())),64)
        for data in ({'rrf_k':float('nan')},{'channels':[]},{'novelty':True}):
            with self.assertRaises(ValueError):RetrievalConfig.from_value(data)
    def test_intervals_are_paired(self):
        self.assertGreater(paired_interval([0,0],[1,1])['lower'],0)
        self.assertEqual(paired_interval([1,1],[1,1])['upper'],0)

    def test_answer_labels_are_specific_to_each_configuration(self):
        row=dict(review_status='reviewed',facts=[],qrels={},answer_review=dict(review_status='reviewed',claims=[dict(status='supported')]))
        self.assertNotIn('claim_support_rate',metrics(row,[]))
        labelled=metrics(row,[],answer_review=dict(review_status='reviewed',claims=[dict(status='unsupported')],citations=[]))
        self.assertEqual(labelled['claim_support_rate'],0)

    def test_no_semantic_winner_without_answer_evidence(self):
        manifest=dict(questions=[dict(id='q',fact_family='f')])
        records=[dict(configuration='current',question_id='q',metrics=dict(formal=True,fact_recall=0,evidence_precision=1)),
                 dict(configuration='B',question_id='q',metrics=dict(formal=True,fact_recall=1,evidence_precision=1))]
        report=comparison_report(records,manifest)
        self.assertEqual(report['comparisons']['B']['status'],'unproven')
