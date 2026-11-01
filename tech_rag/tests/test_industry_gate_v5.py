"""Offline regression checks for the source/clock/inheritance gates.

Fixtures are invented contract examples, never actual company/news facts.
Only external news acquisition is replaced; gates and prepared snapshots run.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration import industry_pipeline as pipeline

AT = '2026-10-19T03:37:09+00:00'
START = '2026-09-19T03:37:09+00:00'
CLOCK = {'analysis_time': AT, 'historical': False}
QUESTION = 'HBM供给与AI服务器内存需求'


def finding(category='search_hypothesis', **link):
    return {'finding_id': 'F1', 'event': 'An input supplier announces a delivery schedule.',
            'event_evidence_ids': ['N1'], 'industry_stage': 'input supply',
            'mechanism': 'Qualified inputs may support a deployment if commissioning succeeds.',
            'direction': 'uncertain', 'company_link': {'relationship_category': category,
            'background_evidence_ids': [], **link}, 'conditions': ['Subject to commissioning.'],
            'unknowns': ['Customer allocation is undisclosed.'], 'basis': 'conditional_hypothesis'}


def audit(value, text='SupplierCo supplies DRAM products to Micron.', context=None, revision=None):
    review = {'finding_id': 'F1', 'decision': 'retain', 'reason': 'Check supplied original evidence.'}
    if revision is not None:
        review.update(decision='revise', revised_finding=revision)
    return pipeline.audit_findings({'findings': [value]}, {'reviews': [review]},
                                   {'N1': {'text': text}}, context)


def background():
    return {'citation_map': {'C1': {'text': 'SupplierCo supplies Micron.'},
                             'C2': {'text': 'Another original paragraph.'}},
            'relationship_categories': {'direct_supply': ['R1'], 'customers': []},
            'assertion_map': {'R1': {'subject_id': 'SupplierCo', 'object_id': 'Micron',
                'category': 'supply', 'directness': 'direct_named_supply',
                'predicate': 'SUPPLIES', 'fact_edge_eligible': True,
                'evidence_class': 'original_disclosure', 'citation_ids': ['C1']}}}


class DirectRelationGateTests(unittest.TestCase):
    def test_nested_relationship_citation_is_checked_and_normalized(self):
        f=finding('technical_cooperation',relation_quote='Company A and Company B will present together.',
                  event_evidence_ids=['N2'])
        news={'N1':{'text':'Original product announcement.'},
              'N2':{'text':'Company A and Company B will present together.'}}
        review={'reviews':[{'finding_id':'F1','decision':'retain'}]}
        out=pipeline.audit_findings({'findings':[f]},review,news,None)
        self.assertEqual(out['findings'][0]['relationship_evidence_ids'],['N2'])
        f['company_link']['event_evidence_ids']=['N_FUTURE']
        self.assertFalse(pipeline.audit_findings({'findings':[f]},review,news,None)['findings'])

    def test_news_manufacturing_service_uses_only_source_declared_abbreviation(self):
        quote='Under the agreement, IC will provide manufacturing service to ClientCo.'
        text='InputCompany (Nasdaq: INP) (IC) announced an agreement. '+quote
        for name in ('InputCompany','InputCompany (IC)'):
            f=finding('direct_supply',relation_quote=quote,relation_subject=name,relation_object='ClientCo')
            self.assertEqual(len(audit(f,text)['findings']),1)
        f=finding('direct_supply',relation_quote=quote,relation_subject='InventedCompany (IC)',relation_object='ClientCo')
        self.assertFalse(audit(f,text)['findings'])
        denied='IC will not provide manufacturing service to ClientCo.'
        f=finding('direct_supply',relation_quote=denied,relation_subject='IC',relation_object='ClientCo')
        self.assertFalse(audit(f,denied)['findings'])

    def test_A_can_use_direct_relation_stated_in_its_news_quote(self):
        f = finding('direct_supply', relation_quote='SupplierCo supplies DRAM products to Micron.',
                    relation_subject='SupplierCo', relation_object='Micron')
        out = audit(f)
        self.assertEqual(len(out['findings']), 1)
        self.assertFalse(out['findings'][0]['relationship_is_established_fact'])

    def test_news_can_explicitly_establish_customer_without_RAG(self):
        f = finding('customers', relation_quote='CloudCo is a customer of NVIDIA.',
                    relation_subject='NVIDIA', relation_object='CloudCo')
        self.assertEqual(len(audit(f, 'CloudCo is a customer of NVIDIA.')['findings']), 1)

    def test_unrelated_background_category_cannot_license_new_relation(self):
        out = audit(finding('direct_supply'), context=background())
        self.assertFalse(out['findings'])
        self.assertIn('direct_relationship_evidence_missing', [i['code'] for i in out['issues']])

    def test_assertion_id_must_be_bound_to_its_actually_cited_evidence(self):
        f = finding('direct_supply', relationship_id='R1', background_evidence_ids=['C1'])
        self.assertEqual(len(audit(f, context=background())['findings']), 1)
        f['company_link']['background_evidence_ids'] = ['C2']
        out = audit(f, context=background())
        self.assertFalse(out['findings'])
        self.assertIn('direct_relationship_citation_mismatch', [i['code'] for i in out['issues']])

    def test_collaboration_assertion_is_not_direct_supply(self):
        context = background()
        context['assertion_map']['R1'].update(category='collaboration', predicate='COLLABORATES_WITH',
                                              directness='technical_collaboration_not_procurement')
        out = audit(finding('direct_supply', relationship_id='R1', background_evidence_ids=['C1']), context=context)
        self.assertFalse(out['findings'])
        self.assertIn('direct_relationship_category_mismatch', [i['code'] for i in out['issues']])

    def test_unavailable_or_nonfact_assertion_cannot_establish_relationship(self):
        for update in ({'fact_edge_eligible': False}, {'evidence_class': 'research_inference'},
                       {'citation_ids': ['MISSING']}):
            with self.subTest(update=update):
                context = background(); context['assertion_map']['R1'].update(update)
                self.assertFalse(audit(finding('direct_supply', relationship_id='R1',
                                  background_evidence_ids=['C1']), context=context)['findings'])

    def test_negation_technical_compatibility_and_invented_quote_are_rejected(self):
        for text, quote in [
            ('SupplierCo does not supply Micron.', 'SupplierCo does not supply Micron.'),
            ('SupplierCo collaborates with Micron for process compatibility.',
             'SupplierCo collaborates with Micron for process compatibility.'),
            ('SupplierCo supplies OtherCo.', 'SupplierCo supplies Micron.'),
            ('SupplierCo and Micron announced technical collaboration, not a supply agreement.',
             'SupplierCo and Micron announced technical collaboration, not a supply agreement.'),
        ]:
            with self.subTest(quote=quote):
                out = audit(finding('direct_supply', relation_quote=quote,
                    relation_subject='SupplierCo', relation_object='Micron'), text, background())
                self.assertFalse(out['findings'])

    def test_declared_endpoints_must_occur_in_the_news_relation_quote(self):
        out = audit(finding('direct_supply', relation_quote='SupplierCo supplies OtherCo.',
                    relation_subject='SupplierCo', relation_object='Micron'),
                    'SupplierCo supplies OtherCo.', background())
        self.assertFalse(out['findings'])
        self.assertIn('direct_relationship_endpoint_mismatch', [i['code'] for i in out['issues']])

    def test_relation_quote_cannot_be_assembled_across_news_paragraphs(self):
        f = finding('direct_supply', relation_quote='SupplierCo supplies Micron.')
        f['event_evidence_ids'] = ['N1', 'N2']
        out = pipeline.audit_findings({'findings': [f]}, {'reviews': [{'finding_id': 'F1', 'decision': 'retain'}]},
                                     {'N1': {'text': 'SupplierCo supplies'}, 'N2': {'text': 'Micron.'}}, background())
        self.assertFalse(out['findings'])


class ReviewInheritanceTests(unittest.TestCase):
    def test_equivalent_revision_keeps_original_conditions_and_unknowns(self):
        original = finding(); revised = deepcopy(original)
        revised['conditions'] = ['Commissioning must first succeed.']
        revised['unknowns'] = ['Allocation to customers remains unknown.']
        out = audit(original, revision=revised)
        self.assertEqual(len(out['findings']), 1)
        kept = out['findings'][0]
        self.assertEqual(kept['inherited_conditions'], ['Subject to commissioning.'])
        self.assertEqual(kept['inherited_unknowns'], ['Customer allocation is undisclosed.'])
        self.assertIn('Subject to commissioning.', kept['conditions'])
        self.assertIn('Commissioning must first succeed.', kept['conditions'])
        self.assertIn('Customer allocation is undisclosed.', kept['unknowns'])
        self.assertIn('Allocation to customers remains unknown.', kept['unknowns'])
        self.assertEqual({i['code'] for i in out['warnings']},
                         {'review_restored_conditions', 'review_restored_unknowns'})
        self.assertEqual(original['conditions'], ['Subject to commissioning.'])

    def test_empty_revision_cannot_erase_required_information(self):
        original = finding(); revised = deepcopy(original)
        revised['conditions'] = []; revised['unknowns'] = []
        out = audit(original, revision=revised)
        self.assertEqual(len(out['findings']), 1)
        self.assertEqual(out['findings'][0]['conditions'], ['Subject to commissioning.'])
        self.assertEqual(out['findings'][0]['unknowns'], ['Customer allocation is undisclosed.'])


class AcquisitionGateTests(unittest.TestCase):
    def prepare(self, tasks, fetch, search=None):
        if search is None:
            search = lambda task, *a, **k: {'items': [
                {'url': 'https://fixture.example/' + task['task_id'] + '/' + str(i), 'title': 'Fixture'}
                for i in range(3)]}
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline, 'plan_search_tasks', return_value=tasks), \
                patch.object(pipeline, 'fetch_article', side_effect=fetch):
            return pipeline.prepare_research('MU', QUESTION, 'current', arm='A', archive_dir=td,
                temporal_context=CLOCK, search_provider=search)

    def tasks(self, count=2):
        return [{'task_id': 'T' + str(i + 1), 'query_en': 'Industry ' + str(i + 1),
                 'time_window': {'start': START, 'end': AT}} for i in range(count)]

    def article(self, item, published='2026-10-18T00:00:00+00:00', eligible=True):
        return {'url': item['url'], 'eligible': eligible, 'body_acquired': eligible,
                'source_verified': eligible, 'status': 'body_verified' if eligible else 'fetch_unavailable',
                'published_at': published, 'body_complete': eligible, 'unknowns': [],
                'body_sha256': 'fixture-body', 'version_id': item['url'],
                'paragraphs': [{'citation_id': 'N_' + item['url'], 'text': 'A complete fixture event body.'}]}

    def test_two_topics_get_round_robin_body_attempts_even_when_first_fails(self):
        def fetch(item, *a, **k):
            return self.article(item, eligible=not item['url'].endswith('T1/0'))
        out = self.prepare(self.tasks(), fetch)
        self.assertEqual([a['search_task_id'] for a in out['articles']], ['T1', 'T2'])
        self.assertEqual(len(out['articles']), 2)
        self.assertFalse(out['articles'][0]['body_acquired'])
        self.assertTrue(out['articles'][1]['body_acquired'])

    def test_original_publication_time_must_be_in_corresponding_task_window(self):
        for published in ['2026-09-19T03:37:08+00:00', '2026-10-19T03:37:10+00:00', None]:
            with self.subTest(published=published):
                out = self.prepare(self.tasks(1), lambda item, *a, **k: self.article(item, published))
                self.assertEqual(out['evidence_map'], {})
                self.assertEqual(len(out['articles']), 2)
                self.assertTrue(all(not a['eligible'] for a in out['articles']))
                self.assertTrue(all(a.get('source_window_eligible') is False for a in out['articles']))

    def test_window_boundaries_are_inclusive_and_provider_timestamp_is_not_substituted(self):
        for published in [START, AT]:
            with self.subTest(published=published):
                out = self.prepare(self.tasks(1), lambda item, *a, **k: self.article(item, published))
                self.assertTrue(out['evidence_map'])


class FrozenSnapshotGateTests(unittest.TestCase):
    def test_online_without_eligible_news_skips_paid_roles_and_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            p=self.prepared(td)
            with patch('integration.industry_models.IndustryModels',side_effect=AssertionError('must not initialize paid provider')):
                out=pipeline.run_industry_research('MU',QUESTION,'current',arm='A',model_mode='online',
                    prepared=p,archive_dir=Path(td)/'run',temporal_context=CLOCK)
            self.assertEqual(out['run_usage']['model_requests'],0)
            self.assertEqual(out['reviewed_output']['answer_status'],'insufficient')
            self.assertEqual(out['stop_reason'],'no_eligible_body_preflight')
            self.assertEqual(out['model_trace'],[])

    def prepared(self, directory):
        return pipeline.prepare_research('MU', QUESTION, 'current', arm='A', archive_dir=directory,
                                         temporal_context=CLOCK, fetch_news=False)

    def run_snapshot(self, prepared, directory, **kwargs):
        return pipeline.run_industry_research('MU', QUESTION, arm='A', model_mode='controlled',
            prepared=prepared, archive_dir=directory, **kwargs)

    def test_prepared_cutoff_cannot_change_between_prepare_and_run(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.prepared(Path(td)/'prepared')
            with self.assertRaisesRegex(ValueError, 'clock|time|cutoff'):
                self.run_snapshot(p, Path(td)/'run', as_of='current',
                         temporal_context={'analysis_time': '2026-10-19T03:37:10+00:00', 'historical': False})

    def test_explicit_historical_timestamp_cannot_relabel_a_current_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.prepared(Path(td)/'prepared')
            with self.assertRaisesRegex(ValueError, 'historical|clock|time'):
                self.run_snapshot(p, Path(td)/'run', as_of=AT)

    def test_valid_current_snapshot_can_be_reused_with_exact_explicit_clock(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.prepared(Path(td)/'prepared')
            out = self.run_snapshot(p, Path(td)/'run', as_of='current', temporal_context=CLOCK)
            self.assertEqual(out['analysis_time'], AT)
            self.assertFalse(out['historical'])

    def test_prepared_budget_is_validated_and_must_match_supplied_budget(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.prepared(Path(td)/'prepared')
            p['budget']['output_tokens'] = 1801
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.run_snapshot(p, Path(td)/'invalid', as_of='current', temporal_context=CLOCK)
            p = self.prepared(Path(td)/'prepared2')
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.run_snapshot(p, Path(td)/'mismatch', as_of='current', temporal_context=CLOCK,
                         budget={'output_tokens': 1500})

    def test_prepared_evidence_or_channel_fingerprint_tampering_is_rejected(self):
        for field in ['evidence_map', 'search_tasks', 'fingerprints']:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                p = self.prepared(Path(td)/'prepared')
                if field == 'evidence_map': p[field]['N_FAKE'] = {'text': 'Altered after freeze.'}
                elif field == 'search_tasks': p[field][0]['query_en'] = 'Altered after freeze'
                else: p[field]['tasks'] = 'incorrect-channel-digest'
                with self.assertRaisesRegex(ValueError, 'fingerprint|snapshot'):
                    self.run_snapshot(p, Path(td)/'run', as_of='current', temporal_context=CLOCK)

    def test_A_rejects_a_frozen_company_background(self):
        with tempfile.TemporaryDirectory() as td:
            try:
                with self.assertRaisesRegex(ValueError, 'A|background|context'):
                    pipeline.prepare_research('MU', QUESTION, 'current', arm='A', fetch_news=False,
                        archive_dir=td, temporal_context=CLOCK, frozen_industry_context={})
            except TypeError as exc:
                self.fail('Frozen IndustryContext validation is missing: ' + str(exc))

    def test_B_reuses_matching_background_without_a_new_RAG_build(self):
        context = {'schema_version': 'industry_context_v5', 'ticker': 'MU', 'research_symbol': 'MU',
                   'question': QUESTION, 'analysis_time': AT, 'historical': False,
                   'temporal_mode': 'current_as_collected', 'citation_map': {}, 'assertion_map': {},
                   'entity_map': {}, 'paths': [], 'unknowns': [], 'fingerprints': {'fixture': 'frozen'}}
        from rag_package import freeze
        context = freeze(context)
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline, 'build_industry_context',
                side_effect=AssertionError('A frozen background must not rebuild company RAG.')):
            try:
                out = pipeline.prepare_research('MU', QUESTION, 'current', arm='B', fetch_news=False,
                    archive_dir=td, temporal_context=CLOCK, frozen_industry_context=context)
            except TypeError as exc:
                self.fail('Frozen IndustryContext reuse is missing: ' + str(exc))
        self.assertEqual(out['industry_context'], context)
        self.assertEqual(out['phase_trace'][0]['phase'], 'frontloaded_company_rag_reused_snapshot')

    def test_B_frozen_background_must_match_company_question_clock_and_mode(self):
        context = {'ticker': 'MU', 'research_symbol': 'MU', 'question': QUESTION,
                   'analysis_time': AT, 'historical': False, 'temporal_mode': 'current_as_collected'}
        for update in [{'ticker': 'TSM', 'research_symbol': 'TSM'}, {'question': 'Another question'},
                       {'analysis_time': '2026-10-19T03:37:10+00:00'}, {'historical': True}]:
            with self.subTest(update=update), tempfile.TemporaryDirectory() as td:
                candidate = {**context, **update}
                try:
                    with self.assertRaisesRegex(ValueError, 'background|context|clock|question|company'):
                        pipeline.prepare_research('MU', QUESTION, 'current', arm='B', fetch_news=False,
                            archive_dir=td, temporal_context=CLOCK, frozen_industry_context=candidate)
                except TypeError as exc:
                    self.fail('Frozen IndustryContext matching is missing: ' + str(exc))


if __name__ == '__main__':
    unittest.main()
