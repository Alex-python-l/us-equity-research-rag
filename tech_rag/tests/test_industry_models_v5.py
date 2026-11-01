"""Local context/request contracts only; controlled output is never quality evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT
from integration.industry_models import IndustryModels, RESEARCH_SCHEMA, system_prompt
from integration.context_codec import unpack_context

AT = '2026-10-19T03:37:09+00:00'


def citation(cid, text='A complete original sentence describing the disclosed business scope.'):
    return dict(citation_id=cid, source_id='SRC', source_sha256='a'*64,
        source_url='https://example.test/original', title='Original business disclosure',
        available_at='2026-10-01T00:00:00+00:00', historical_eligible=True,
        pdf_page=None, paragraph=1, char_start=0, char_end=len(text), text=text,
        round_trip_verified=True, coordinate_system='normalized Unicode half-open interval')


def relation(rid, refs, *, predicate='COLLABORATES_WITH', category='collaboration'):
    return dict(relation_id=rid, subject_id='MU', predicate=predicate, object_id='TSM',
        category=category, directness='technical_collaboration_not_procurement',
        direction='not_applicable', evidence_class='original_disclosure', fact_edge_eligible=True,
        claim='The companies disclose technical compatibility work.',
        business_scope='Declared product compatibility; no orders or quantities disclosed.',
        conditions='Only compatibility cooperation; no procurement contract is established.',
        available_at='2026-10-01T00:00:00+00:00', historical_eligible=True,
        valid_from=None, valid_to=None, temporal_scope='As disclosed; no perpetual continuation.',
        citation_ids=refs)


def background():
    return dict(research_symbol='MU', analysis_time=AT,
        citation_map={'C_ONE':citation('C_ONE')}, assertion_map={'R_ONE':relation('R_ONE',['C_ONE'])},
        relationship_categories={'technical_cooperation':['R_ONE'], 'direct_supply':[]},
        business_and_products=[], constraints=[], paths=[],
        unknowns=['No third-party business graph is available.'], fingerprints={}, coverage={})


def prepared(context=None, tasks=None):
    title='Complete original news heading: power, cooling and commissioning conditions'
    base=dict(version_id='NEW_fixture', url='https://example.test/news', title=title,
        publisher='Fixture publisher', published_at='2026-10-18T00:00:00+00:00',
        body_sha256='b'*64, heading_path=[title], round_trip_verified=True,
        evidence_kind='verified_news_body')
    news={
        'N_HEAD':dict(base, citation_id='N_HEAD', text=title, kind='heading', paragraph=1),
        'N_BODY':dict(base, citation_id='N_BODY',
            text='Commissioning is conditional on completed power and cooling works.',
            kind='paragraph', paragraph=2),
    }
    return dict(ticker='MU', arm='B' if context else 'A', question='Which recent industry changes need verification?',
        analysis_time=AT, historical=False, availability_mode='current_as_collected',
        unknowns=[], temporal_limit='Collected snapshot; no verified historical vintage.',
        industry_context=context, search_tasks=tasks or [], evidence_map=news,
        budget={'output_tokens':1800,'context_chars':24000})


class IndustryModelContextTests(unittest.TestCase):
    def test_task_relation_reserves_all_quotes_before_optional_large_quote(self):
        context=background()
        context['citation_map']={'C_FILLER':citation('C_FILLER','Unrelated complete sentence. '*210),
                                 **context['citation_map']}
        tasks=[{'background_evidence_ids':['C_FILLER','C_ONE'],
                'query_basis':{'supporting_relation_ids':['R_ONE']}}]
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(context,tasks),tmp)
        compact=models.context['background']
        self.assertIn('R_ONE',compact.get('assertion_map',{}))
        self.assertEqual(compact['citation_map']['C_ONE']['text'],context['citation_map']['C_ONE']['text'])
        self.assertNotIn('C_FILLER',compact['citation_map'])
        row=compact['assertion_map']['R_ONE']
        for key in ('subject_id','predicate','object_id','category','directness','direction',
                    'evidence_class','claim','conditions','business_scope','available_at',
                    'valid_from','valid_to','temporal_scope','citation_ids'):
            self.assertEqual(row[key],context['assertion_map']['R_ONE'][key])
        self.assertLessEqual(len(json.dumps(compact,ensure_ascii=False,separators=(',',':'))),7000)

    def test_missing_supporting_quote_omits_relation_and_its_category_reference(self):
        context=background();context['assertion_map']['R_ONE']['citation_ids']=['C_ONE','C_MISSING']
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(context),tmp)
        compact=models.context['background']
        self.assertNotIn('R_ONE',compact.get('assertion_map',{}))
        self.assertNotIn('R_ONE',compact['relationship_categories']['technical_cooperation'])
        self.assertTrue(any(row.get('relation_id')=='R_ONE' for row in models.omissions))

    def test_future_and_out_of_scope_relations_do_not_enter_model_background(self):
        context=background()
        context['assertion_map']['R_ONE']['available_at']='2026-10-20T00:00:00+00:00'
        context['citation_map']['C_FUTURE']=citation('C_FUTURE','Future-only source assertion.')
        context['citation_map']['C_FUTURE']['available_at']='2026-10-20T00:00:00+00:00'
        context['assertion_map']['R_FUTURE_QUOTE']=relation('R_FUTURE_QUOTE',['C_FUTURE'])
        context['assertion_map']['R_EXPIRED']=relation('R_EXPIRED',['C_ONE'])
        context['assertion_map']['R_EXPIRED']['valid_to']='2026-09-30'
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(context),tmp)
        compact=models.context['background']
        self.assertEqual(compact.get('assertion_map',{}),{})
        self.assertNotIn('C_FUTURE',compact['citation_map'])
        self.assertNotIn('Future-only source assertion.',json.dumps(models.context))
        self.assertGreaterEqual(len([o for o in models.omissions if o.get('relation_id')]),3)

    def test_oversized_relation_bundle_is_omitted_whole(self):
        context=background()
        text='The entire original sentence must remain intact. '*190
        context['citation_map']['C_HUGE']=citation('C_HUGE',text)
        context['assertion_map']={'R_HUGE':relation('R_HUGE',['C_HUGE']),**context['assertion_map']}
        tasks=[{'query_basis':{'supporting_relation_ids':['R_HUGE','R_ONE']}}]
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(context,tasks),tmp)
        compact=models.context['background']
        self.assertNotIn('R_HUGE',compact.get('assertion_map',{}))
        self.assertNotIn('C_HUGE',compact['citation_map'])
        self.assertIn('R_ONE',compact.get('assertion_map',{}))
        self.assertTrue(any(o.get('relation_id')=='R_HUGE' and 'budget' in o['reason'] for o in models.omissions))

    def test_business_constraint_and_ordered_path_indices_have_no_dangling_relations(self):
        context=background()
        context['assertion_map'].update(R_BUSINESS=relation('R_BUSINESS',['C_ONE'],predicate='HAS_BUSINESS',category='business'),
            R_CONSTRAINT=relation('R_CONSTRAINT',['C_ONE'],predicate='CONSTRAINED_BY',category='constraint'))
        context['business_and_products']=['R_BUSINESS'];context['constraints']=['R_CONSTRAINT']
        context['paths']=[dict(path_id='PATH',nodes=['MU','TSM','RESOURCE'],path_type='disclosed_relation_path',
            procurement_implied_by_reachability=False,causal_effect_estimated=False,
            steps=[dict(order=1,from_id='MU',to_id='TSM',relation_id='R_ONE',
                        traversal_direction='with_assertion',citation_ids=['C_ONE']),
                   dict(order=2,from_id='TSM',to_id='RESOURCE',relation_id='R_CONSTRAINT',
                        traversal_direction='against_assertion',citation_ids=['C_ONE'])])]
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(context),tmp)
        compact=models.context['background']
        self.assertEqual(compact.get('business_and_products'),['R_BUSINESS'])
        self.assertEqual(compact.get('constraints'),['R_CONSTRAINT'])
        self.assertEqual(compact.get('paths',[{}])[0].get('path_id'),'PATH')
        self.assertEqual([s['relation_id'] for s in compact['paths'][0]['steps']],['R_ONE','R_CONSTRAINT'])
        self.assertEqual(compact['paths'][0]['steps'][1]['traversal_direction'],'against_assertion')
        self.assertFalse(compact['paths'][0]['procurement_implied_by_reachability'])
        self.assertIn('No third-party business graph is available.',compact['unknowns'])
        self.assertNotIn('entity_map',compact)

    def test_real_mu_and_tsm_typed_relations_reach_selected_context_and_request(self):
        base=ROOT/'industry_v5_20261019'
        preview=json.loads((base/'tasks_preview.json').read_text(encoding='utf-8'))
        for ticker in ('MU','TSM'):
            with self.subTest(ticker=ticker), TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
                context=json.loads((base/'background'/(ticker+'.json')).read_text(encoding='utf-8'))
                case=next(c for c in preview['cases'] if c['ticker']==ticker)
                p=prepared(context,case['B']);p.update(ticker=ticker,question=case['question'])
                models=IndustryModels(p,tmp,mode='controlled')
                selected=json.loads((Path(tmp)/'selected_context.json').read_text(encoding='utf-8'))['context']
                self.assertTrue(selected['background'].get('assertion_map'))
                typed=selected['background']['assertion_map']
                priority=[rid for task in case['B'] for rid in task['query_basis']['supporting_relation_ids']]
                self.assertTrue(set(priority)&set(typed))
                models.invoke('research')
                payload=json.loads((Path(tmp)/'01_research_input.json').read_text(encoding='utf-8'))['request']
                sent=unpack_context(json.loads(payload['messages'][1]['content'].split('\n',1)[1]))
                self.assertEqual(sent['background'],selected['background'])
                self.assertEqual(payload['max_tokens'],1800)
                self.assertEqual(models.usage()['model_requests'],0)
                for rid,row in typed.items():
                    original=context['assertion_map'][rid]
                    for key in ('subject_id','predicate','object_id','category','direction','conditions','citation_ids'):
                        self.assertEqual(row[key],original[key])
                    self.assertTrue(set(row['citation_ids'])<=set(sent['background']['citation_map']))
                for cid,row in sent['background']['citation_map'].items():
                    self.assertEqual(row['text'],context['citation_map'][cid]['text'])
                for cid,item in p['evidence_map'].items():
                    source=sent['news_source_versions'][item['version_id']]
                    self.assertEqual({**source,**sent['news_evidence'][cid],'citation_id':cid},item)
                self.assertEqual(sent['news_evidence']['N_HEAD']['kind'],'heading')
                self.assertEqual(sent['news_evidence']['N_BODY']['heading_path'],p['evidence_map']['N_BODY']['heading_path'])

    def test_schema_is_blank_and_allows_news_grounded_relation_details_without_rag(self):
        link=RESEARCH_SCHEMA['findings'][0]['company_link']
        for key in ('relationship_id','relation_quote','relation_subject','relation_object'):
            self.assertIn(key,link)
        self.assertEqual(RESEARCH_SCHEMA['findings'][0]['event'],'string')
        self.assertEqual(RESEARCH_SCHEMA['findings'][0]['mechanism'],'string')
        self.assertEqual(link['explanation'],'string')
        for role in ('research','reviewer'):
            prompt=system_prompt(role)
            self.assertNotIn('TR_',prompt);self.assertNotIn('IND5_',prompt)
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(prepared(),tmp)
            models.invoke('research')
            payload=json.loads((Path(tmp)/'01_research_input.json').read_text(encoding='utf-8'))['request']
        self.assertIsNone(models.context['background'])
        self.assertEqual(payload['max_tokens'],1800)
        self.assertFalse(models.traces[0]['http_request_attempted'])

    def test_whole_news_article_including_headings_is_omitted_when_it_cannot_fit(self):
        p=prepared()
        p['evidence_map']['N_BODY']['text']='A complete long article sentence. '*600
        with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
            models=IndustryModels(p,tmp)
        self.assertEqual(models.delivered_news,{})
        self.assertTrue(any(o.get('version_id')=='NEW_fixture' and o['citation_ids']==['N_HEAD','N_BODY'] for o in models.omissions))
        self.assertEqual(p['evidence_map']['N_HEAD']['text'],p['evidence_map']['N_HEAD']['title'])


if __name__=='__main__':unittest.main()
