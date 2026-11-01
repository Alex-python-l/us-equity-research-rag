"""Industry planning contracts; only the unified retrieval seam is replaceable."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AT = '2026-10-19T03:37:09+00:00'
C = 'C_' + '1' * 24


def packet_fixture():
    text = ('Micron makes DRAM and HBM4 memory. Micron and TSMC collaborate on '
            'CoWoS advanced packaging. NVIDIA purchases memory from Micron. '
            'Qualified HBM supply may constrain delivery if inventory is insufficient.')
    citation = dict(citation_id=C, chunk_id='TC_fixture', source_id='SRC',
                    source_sha256='a' * 64, source_url='https://example.test/disclosure',
                    title='Dated disclosure', report_period='2026', published_at=None,
                    actual_public_at=None, availability_basis='archived disclosure',
                    available_at='2026-10-10T12:00:00+00:00', historical_eligible=True,
                    pdf_page=None, paragraph=3, char_start=0, char_end=len(text), text=text,
                    coordinate_system='normalized whitespace Unicode, half-open [start,end)',
                    round_trip_verified=True, review_status='raw_original_unreviewed_as_relation',
                    overlapping_reviewed_evidence_ids=['EV_fixture'], excerpt_truncated=False)
    assertions = {}
    rows = [
        ('business', 'MU', 'MANUFACTURES', 'DRAM', 'business', 'not_a_supply_relation', 'original_disclosure'),
        ('product', 'MU', 'OFFERS', 'MU_HBM4', 'business', 'not_a_supply_relation', 'original_disclosure'),
        ('supply', 'MU', 'SUPPLIES_TO', 'NVDA', 'supply', 'direct_named_procurement', 'original_disclosure'),
        ('cooperation', 'MU', 'COLLABORATES_WITH', 'TSM', 'collaboration', 'technical_collaboration_not_procurement', 'original_disclosure'),
        ('dependency', 'MU', 'DEPENDS_ON_INPUT', 'CHEM_GAS', 'technical_dependency', 'not_a_supply_relation', 'original_disclosure'),
        ('constraint', 'MU', 'CONSTRAINED_BY', 'MU_YIELD', 'constraint', 'not_a_supply_relation', 'original_disclosure'),
        ('inference', 'HBM_SUPPLY', 'MAY_AFFECT', 'MU', 'impact_mechanism', 'not_a_supply_relation', 'model_inference'),
    ]
    for rid, subject, predicate, obj, category, directness, evidence_class in rows:
        assertions[rid] = dict(relation_id=rid, subject_id=subject, predicate=predicate,
            object_id=obj, claim={'cooperation':'HBM and CoWoS technical collaboration',
                                 'product':'HBM4 memory product'}.get(rid, text),
            category=category, directness=directness, evidence_class=evidence_class,
            fact_edge_eligible=evidence_class == 'original_disclosure',
            conditions='only within the disclosed product scope', business_scope='2026 disclosure',
            available_at=citation['available_at'], historical_eligible=True,
            review_status='curated_with_matched_evidence', evidence_ids=['EV_fixture'],
            citation_ids=[C], source_ids=['SRC'])
    return dict(schema_version='tech_evidence_packet_v2', dataset_id='tech_company_v1',
        company={'entity_id':'MU', 'name':'Micron Technology, Inc.'}, research_symbol='MU',
        question='HBM4 memory and CoWoS capacity changes', analysis_time=AT,
        temporal_mode='current_as_collected', status='partial', backend='local',
        citations={C:citation}, assertion_map=assertions,
        evidence_map={'EV_fixture':dict(evidence_id='EV_fixture', citation_ids=[C], fully_covered=True)},
        paths=[dict(path_id='RP_fixture', nodes=['MU', 'TSM'], steps=[dict(relation_id='cooperation', citation_ids=[C])],
                    procurement_implied_by_reachability=False, causal_effect_estimated=False)],
        results=[dict(citation_id=C, chunk_id='TC_fixture', rank=1)],
        company_profile={'main_business':['business','product']},
        budget={'max_total_chars':14000, 'use_dense':False},
        budget_usage={'text_chars':len(text), 'retrieval_tool_calls':2},
        pagination={'page':1, 'next_cursor':'next_fixture', 'resume_available':True},
        coverage={'truncated':True, 'exhaustive':False, 'truncation':{'result_page':True}},
        fingerprints={'eligible_data':'fixture-data', 'eligible_keyword_corpus':'fixture-corpus',
                      'embedding_model':'local existing model'},
        unknowns=['actual inventory and delivery windows'], insufficiency_reasons=[], trace=[])


class PacketEngine:
    def __init__(self, packet=None):
        self.packet = packet or packet_fixture()
        self.calls = []
        self.tools = SimpleNamespace(entities={
            key:dict(entity_id=key, name=name, kind=kind) for key, name, kind in [
                ('MU','Micron','Company'), ('NVDA','NVIDIA','Company'), ('TSM','TSMC','Company'),
                ('DRAM','DRAM memory','Product'), ('MU_HBM4','Micron HBM4','Product'),
                ('CHEM_GAS','chemicals and gases','Resource'), ('MU_YIELD','yield','Constraint'),
                ('HBM_SUPPLY','HBM supply','Resource')]})

    def build(self, ticker, question, as_of, budget=None, **kwargs):
        self.calls.append(dict(ticker=ticker, question=question, as_of=as_of, budget=budget, **kwargs))
        return deepcopy(self.packet)


class ModuleTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('integration.industry_context'),
                             'IndustryContext and SearchTask public API is missing')
        from integration import industry_context
        return industry_context


class IndustryContextTests(ModuleTests):
    def test_context_preserves_packet_and_exact_citation_identity(self):
        engine = PacketEngine()
        context = self.module().build_industry_context('MU', engine.packet['question'], AT, engine=engine)
        json.dumps(context, allow_nan=False)
        self.assertEqual(context['evidence_packet'], engine.packet)
        self.assertEqual(context['citation_map'][C], engine.packet['citations'][C])
        self.assertEqual(context['citation_map'][C]['char_end'], len(engine.packet['citations'][C]['text']))
        self.assertEqual(context['fingerprints']['eligible_data'], 'fixture-data')
        self.assertTrue(context['source_versions'])
        self.assertTrue(context['coverage']['truncated'])

    def test_context_keeps_cooperation_dependency_and_inference_out_of_supply(self):
        context = self.module().build_industry_context('MU','HBM4 CoWoS capacity',AT,engine=PacketEngine())
        categories = context['relationship_categories']
        self.assertEqual(categories['direct_supply'], ['supply'])
        self.assertIn('supply', categories['customers'])
        self.assertEqual(categories['technical_cooperation'], ['cooperation'])
        self.assertEqual(categories['industry_dependency'], ['dependency'])
        self.assertEqual(categories['inference'], ['inference'])
        self.assertEqual(context['business_and_products'], ['business','product'])
        self.assertEqual(context['constraints'], ['constraint'])
        self.assertFalse(context['paths'][0]['procurement_implied_by_reachability'])
        self.assertTrue(context['unknowns'])

    def test_front_load_is_one_unified_call_with_dense_opt_in_and_hard_excerpt_cap(self):
        module = self.module(); engine = PacketEngine()
        clock = {'analysis_time':AT, 'historical':False}
        module.build_industry_context('MU','HBM4 capacity','current',
            {'max_total_chars':22000},engine=engine,temporal_context=clock)
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(engine.calls[0]['mode'],'unified')
        self.assertFalse(engine.calls[0]['budget'].use_dense)
        self.assertEqual(engine.calls[0]['budget'].max_total_chars,14000)
        self.assertEqual(engine.calls[0]['temporal_context'],clock)
        module.build_industry_context('MU','HBM4 capacity',AT,{'use_dense':True},engine=engine)
        self.assertTrue(engine.calls[-1]['budget'].use_dense)

    def test_real_engine_context_is_dated_traceable_and_bounded(self):
        module = self.module()
        from common import ROOT, parse_time
        from research import ResearchEngine
        class NoDenseEngine(ResearchEngine):
            def dense_search(self, *args, **kwargs):
                raise AssertionError('Default IndustryContext must not initialize/query a model')
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            engine = NoDenseEngine(index_path=Path(tmp)/'lexical.sqlite3')
            context = module.build_industry_context('MU',
                '近期产业新闻中哪些变化可通过HBM/DRAM有效供给与扩产兑现节奏、AI服务器内存需求与产品组合，'
                '影响美光未来6至12个月的供需、成本或产能？请解释条件性传导路径并列出未知。',
                'current',engine=engine,temporal_context={'analysis_time':AT,'historical':False})
        self.assertTrue(context['citation_map'])
        self.assertTrue(context['assertion_map'], 'Original quotes must leave room for reviewed background relations')
        self.assertTrue(context['business_and_products'])
        self.assertEqual(context['analysis_time'],AT)
        self.assertEqual(context['temporal_mode'],'current_as_collected')
        self.assertLessEqual(sum(len(c['text']) for c in context['citation_map'].values()),14000)
        for cid, citation in context['citation_map'].items():
            self.assertEqual(cid,citation['citation_id'])
            self.assertTrue(citation['round_trip_verified'])
            self.assertEqual(citation['char_end']-citation['char_start'],len(citation['text']))
            self.assertLessEqual(parse_time(citation['available_at']),parse_time(AT))
            self.assertEqual(len(citation['source_sha256']),64)
        for assertion in context['assertion_map'].values():
            self.assertTrue(set(assertion['citation_ids']) <= set(context['citation_map']))

    def test_context_id_is_stable_across_cache_trace_changes(self):
        module=self.module(); one=PacketEngine(); two=PacketEngine()
        two.packet['trace']=[{'tool':'reuse_frozen_candidate_snapshot'}]
        two.packet['budget_usage']['retrieval_tool_calls']=0
        a=module.build_industry_context('MU','HBM4 memory',AT,engine=one)
        b=module.build_industry_context('MU','HBM4 memory',AT,engine=two)
        self.assertEqual(a['context_id'],b['context_id'])

    def test_uncovered_background_sections_are_visible_as_gaps_not_absence_claims(self):
        packet=packet_fixture();packet['assertion_map']={};packet['paths']=[]
        context=self.module().build_industry_context('MU','HBM4 memory',AT,engine=PacketEngine(packet))
        self.assertIn('business_and_products',context['coverage'].get('missing_background_sections',[]))
        self.assertIn('direct_supply',context['coverage'].get('missing_background_sections',[]))
        self.assertGreater(len(context['unknowns']),len(packet['unknowns']))
        self.assertEqual(context['relationship_categories']['direct_supply'],[])


class SearchTaskTests(ModuleTests):
    def context(self):
        return self.module().build_industry_context('MU','HBM4 memory and CoWoS capacity changes',AT,engine=PacketEngine())

    def test_without_background_tasks_use_question_industry_terms_as_hypotheses(self):
        tasks=self.module().plan_search_tasks('MU','近30天HBM内存供需与先进封装产能有什么变化？',AT,lookback_days=30)
        self.assertEqual(len(tasks),2)
        for task in tasks:
            self.assertEqual(task['query_basis']['classification'],'search_hypothesis')
            self.assertEqual(task['background_evidence_ids'],[])
            self.assertNotIn(task['query_en'],('MU','Micron'))
            self.assertTrue(any(word.lower() in task['query_en'].lower() for word in ('HBM','packaging')))
            self.assertTrue(task['potential_impact_path']['conditional'])
            self.assertTrue(task['conditions']); self.assertTrue(task['unknowns'])
            self.assertEqual(task['time_window'],{'start':'2026-09-19T03:37:09+00:00','end':AT,'lookback_days':30})

    def test_background_tasks_resolve_evidence_and_products_without_fact_prefill(self):
        context=self.context()
        tasks=self.module().plan_search_tasks('MU','HBM4内存和先进封装近期供给变化',AT,context)
        self.assertEqual(len(tasks),2)
        self.assertTrue(any('CoWoS' in task['query_en'] for task in tasks))
        self.assertTrue(any('HBM4' in task['query_en'] for task in tasks))
        for task in tasks:
            self.assertEqual(task['query_basis']['classification'],'evidence_derived_search_direction')
            self.assertEqual(task['background_evidence_ids'],[C])
            self.assertTrue(set(task['potential_impact_path']['background_relation_ids']) <= set(context['assertion_map']))
            self.assertNotIn('claim',task);self.assertNotIn('gold',task)
            self.assertEqual(task['potential_impact_path']['status'],'conditional_search_direction')

    def test_uncovered_topic_stays_hypothesis_even_with_unrelated_background(self):
        context=self.context()
        for citation in context['citation_map'].values():citation['text']='CoWoS advanced packaging technical alliance.'
        context['assertion_map']={'cooperation':context['assertion_map']['cooperation']}
        context['assertion_map']['cooperation']['claim']='CoWoS technical alliance'
        tasks=self.module().plan_search_tasks('MU','HBM4供需变化',AT,context,max_queries=1)
        self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['background_evidence_ids'],[])
        self.assertEqual(tasks[0]['query_basis']['classification'],'search_hypothesis')

    def test_same_industry_query_hash_is_reusable_across_companies(self):
        module=self.module(); question='HBM内存供需变化'
        a=module.plan_search_tasks('MU',question,AT,max_queries=1)
        b=module.plan_search_tasks('NVDA',question,AT,max_queries=1)
        self.assertEqual(a[0]['query_hash'],b[0]['query_hash'])
        self.assertNotEqual(a[0]['task_id'],b[0]['task_id'])
        self.assertNotIn('MU',a[0]['query_en'])
        c=module.plan_search_tasks('MU',question,'2026-10-19T04:37:09+00:00',max_queries=1)
        self.assertNotEqual(a[0]['query_hash'],c[0]['query_hash'])

    def test_future_background_is_not_used_to_derive_a_search_direction(self):
        context=self.context(); context['citation_map'][C]['available_at']='2026-10-20T00:00:00+00:00'
        tasks=self.module().plan_search_tasks('MU','HBM4供需变化',AT,context,max_queries=1)
        self.assertEqual(tasks[0]['background_evidence_ids'],[])
        self.assertEqual(tasks[0]['query_basis']['classification'],'search_hypothesis')

    def test_product_generation_without_exact_background_stays_hypothesis(self):
        context=self.context()
        for row in context['citation_map'].values():row['text']=row['text'].replace('HBM4','HBM3E')
        tasks=self.module().plan_search_tasks('MU','HBM4供给变化',AT,context,max_queries=1)
        self.assertEqual(tasks[0]['background_evidence_ids'],[])
        self.assertIn('HBM4',tasks[0]['query_en'])
        self.assertNotIn('HBM3E',tasks[0]['query_en'])
        self.assertEqual(tasks[0]['query_basis']['classification'],'search_hypothesis')

    def test_planner_abstains_without_question_industry_terms_and_bounds_tasks(self):
        module=self.module()
        self.assertEqual(module.plan_search_tasks('MU','MU现在如何？',AT),[])
        self.assertLessEqual(len(module.plan_search_tasks('MU','HBM DRAM NAND CoWoS policy power capacity',AT,max_queries=20)),2)
        self.assertEqual(module.plan_search_tasks('MU','HBM supply',AT,max_queries=0),[])
        with self.assertRaises(ValueError):module.plan_search_tasks('MU','HBM supply','2026-10-19')
        with self.assertRaises(ValueError):module.plan_search_tasks('NVDA','HBM supply',AT,self.context())
        with self.assertRaises(ValueError):module.plan_search_tasks('MU','HBM supply',AT,lookback_days=0)


if __name__ == '__main__':
    unittest.main()
