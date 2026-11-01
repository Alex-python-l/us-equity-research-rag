import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AT = '2026-10-19T03:37:09+00:00'


class IndustryPipelineTests(unittest.TestCase):
    def test_explicit_source_followup_tasks_are_bounded_and_never_invent_A_background(self):
        m=self.module()
        task={'task_id':'P1','query_en':'data center power','query_zh':'数据中心电力',
              'query_hash':'p1','time_window':{'start':'2026-09-19T03:37:09+00:00','end':AT},
              'background_evidence_ids':[],'query_basis':{'classification':'search_hypothesis'}}
        with tempfile.TemporaryDirectory() as td:
            got=m.prepare_research('NVDA','数据中心电力','current',arm='A',fetch_news=False,
                archive_dir=td,frozen_search_tasks=[task],
                temporal_context={'analysis_time':AT,'historical':False})
            self.assertEqual(got['search_tasks'][0]['query_en'],'data center power')
            bad={**task,'background_evidence_ids':['invented_C']}
            with self.assertRaises(ValueError):m.prepare_research('NVDA','数据中心电力','current',arm='A',
                fetch_news=False,archive_dir=td,frozen_search_tasks=[bad],
                temporal_context={'analysis_time':AT,'historical':False})

    def module(self):
        from integration import industry_pipeline
        return industry_pipeline

    def test_rag_is_before_search_and_never_after_news(self):
        m = self.module(); order = []
        context = {'schema_version': 'IndustryContext', 'citation_map': {}, 'paths': [],
                   'unknowns': ['no confirmed supplier'], 'fingerprints': {}}
        def bg(*a, **k): order.append('RAG'); return context
        def tasks(*a, **k): order.append('tasks'); return [{'task_id': 'T1', 'query_en': 'HBM capacity'}]
        def search(*a, **k): order.append('search'); return {'items': [], 'status': 'no_eligible_candidates'}
        with tempfile.TemporaryDirectory() as td, patch.object(m, 'build_industry_context', side_effect=bg), patch.object(m, 'plan_search_tasks', side_effect=tasks):
            got = m.prepare_research('MU', 'HBM capacity', 'current', archive_dir=td,
                                     search_provider=search, fetch_news=True,
                                     temporal_context={'analysis_time': AT, 'historical': False})
        self.assertEqual(order, ['RAG', 'tasks', 'search'])
        self.assertEqual(got['articles'], [])
        self.assertEqual(got['status'], 'insufficient')
        self.assertEqual(got['company_rag_calls_after_news'], 0)
        self.assertEqual(got['analysis_time'], AT)

    def test_A_has_no_background_call_but_has_industry_query(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as td, patch.object(m, 'build_industry_context', side_effect=AssertionError('A called RAG')):
            got = m.prepare_research('MU', 'HBM供给和AI服务器内存需求', 'current', arm='A',
                                     archive_dir=td, fetch_news=False)
        self.assertIsNone(got['industry_context'])
        self.assertTrue(got['search_tasks'])
        self.assertNotEqual(got['search_tasks'][0]['query_en'], 'MU')

    def test_historical_never_calls_live_search_or_falls_back_to_old_event(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as td:
            got = m.prepare_research('MU', 'HBM', '2024-06-01T00:00:00+00:00', arm='A',
                                     archive_dir=td, fetch_news=True,
                                     search_provider=lambda *a, **k: self.fail('historical network'))
        self.assertTrue(got['historical']); self.assertEqual(got['articles'], [])
        self.assertEqual(got['status'], 'insufficient')
        self.assertEqual(got['evidence_map'], {})

    def test_correct_paraphrase_keeps_research_content_with_supporting_ids(self):
        m = self.module()
        evidence = {'N1': {'text': 'The facility will open subject to power availability.'}}
        finding = {'finding_id': 'f1', 'event': '设施能否投运取决于供电', 'event_evidence_ids': ['N1'],
                   'industry_stage': 'data-center power', 'mechanism': '投运可能影响算力部署',
                   'direction': 'conditional', 'company_link': {'relationship_category': 'search_hypothesis',
                   'background_evidence_ids': [], 'explanation': '具体传导尚待核验'},
                   'conditions': ['power available'], 'unknowns': ['orders not disclosed'],
                   'basis': 'conditional_hypothesis'}
        reviewed = {'reviews': [{'finding_id': 'f1', 'decision': 'retain', 'reason': '支持且有条件', 'revised_finding': None}], 'unknowns': []}
        safe = m.audit_findings({'findings': [finding], 'unknowns': []}, reviewed, evidence, None)
        self.assertEqual(len(safe['findings']), 1)
        self.assertIn('设施', safe['findings'][0]['event'])
        self.assertTrue(safe['semantic_review_is_model_assisted_not_proof'])

    def test_missing_ids_and_specific_model_upgrade_do_not_become_safe_facts(self):
        m = self.module()
        f = {'finding_id': 'f', 'event': 'HBM4 shipments increased', 'event_evidence_ids': ['N1'],
             'conditions': ['if supplied'], 'unknowns': [], 'basis': 'research_interpretation',
             'company_link': {'background_evidence_ids': [], 'relationship_category': 'search_hypothesis'}}
        review = {'reviews': [{'finding_id': 'f', 'decision': 'retain'}]}
        out = m.audit_findings({'findings': [f]}, review, {'N1': {'text': 'Memory shipments increased.'}}, None)
        self.assertFalse(out['findings'])
        self.assertIn('event_product_model_not_in_news', [i['code'] for i in out['issues']])
        f['event_evidence_ids'] = ['MISSING']
        out = m.audit_findings({'findings': [f]}, review, {}, None)
        self.assertFalse(out['findings'])

    def test_reviewer_cannot_drop_necessary_conditions_or_unknowns(self):
        m = self.module()
        original = {'finding_id': 'f', 'event': 'Power facility approved', 'event_evidence_ids': ['N1'],
                    'conditions': ['construction and grid connection'], 'unknowns': ['orders unknown'],
                    'basis': 'conditional_hypothesis',
                    'company_link': {'relationship_category': 'search_hypothesis', 'background_evidence_ids': []}}
        revised = dict(original, conditions=[])
        out = m.audit_findings({'findings': [original]}, {'reviews': [{'finding_id': 'f', 'decision': 'revise', 'revised_finding': revised}]}, {'N1': {'text': 'Power facility approved'}}, None)
        self.assertEqual(len(out['findings']), 1)
        self.assertIn('construction and grid connection', out['findings'][0]['conditions'])
        self.assertIn('orders unknown', out['findings'][0]['unknowns'])
        self.assertEqual(out['findings'][0]['inherited_conditions'], ['construction and grid connection'])
        self.assertEqual(out['findings'][0]['inherited_unknowns'], ['orders unknown'])
        self.assertIn('review_restored_conditions', [i['code'] for i in out['warnings']])

    def test_normal_framework_entry_dispatches_to_the_frontloaded_pipeline(self):
        self.module()
        from integration.framework import run_research
        with patch('integration.industry_pipeline.run_industry_research', return_value={'new_pipeline': True}) as called:
            self.assertTrue(run_research('MU', 'HBM', use_dense=False)['new_pipeline'])
        called.assert_called_once()


if __name__ == '__main__': unittest.main()
