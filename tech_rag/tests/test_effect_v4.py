"""Effect experiments must be budgeted and auditable before any paid call."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class CostTests(unittest.TestCase):
    def test_context_repair_counts_prefix_and_discarded_cost_without_extra_complete_case(self):
        from integration.effect_cost import BatchBudget,BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            b=BatchBudget(Path(tmp)/'ledger.json',max_runs=1,per_run=3,max_requests=6,max_tokens=100)
            b.start_run('case',{})
            prefix=b.reserve('case','news');b.complete(prefix,{'total_tokens':1},'ok')
            discarded=b.reserve('case','risk');b.complete(discarded,{'total_tokens':1},'ok')
            b.finish_run('case','provider_or_budget_incomplete')
            b.start_repair_branch('repair','case',[prefix])
            for role in ('bear','manager'):
                rid=b.reserve('repair',role);b.complete(rid,{'total_tokens':1},'ok')
            with self.assertRaises(BudgetStop):b.reserve('repair','trader')
            with self.assertRaises(BudgetStop):b.start_repair_branch('repair2','case',[prefix])
            self.assertEqual(b.read()['request_count'],4)
            self.assertEqual(len(b.read()['runs']),1)
            self.assertEqual(b.read()['total_tokens'],4)

    def test_replay_requires_identical_entire_request_and_preserves_provider_file(self):
        from integration.effect_models import recorded_response
        from integration.effect_cost import BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);a=folder/'input.json';b=folder/'provider.json'
            payload={'model':'same','messages':[{'role':'user','content':'actual evidence'}]}
            a.write_text(json.dumps({'request_id':'old','request':payload}),encoding='utf-8')
            b.write_text(json.dumps({'usage':{'total_tokens':10},'choices':[]}),encoding='utf-8')
            before=b.read_bytes()
            self.assertEqual(recorded_response(a,b,payload)[0],'old')
            with self.assertRaises(BudgetStop):recorded_response(a,b,{**payload,'model':'other'})
            self.assertEqual(b.read_bytes(),before)

    def test_failed_or_unknown_usage_prevents_an_unaccounted_retry(self):
        from integration.effect_cost import BatchBudget, BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            b=BatchBudget(Path(tmp)/'ledger.json',max_requests=3,max_tokens=10,max_runs=2)
            b.start_run('r',{})
            rid=b.reserve('r','news')
            with self.assertRaises(BudgetStop):b.reserve('r','news')
            b.complete(rid,None,'network_failure')
            with self.assertRaises(BudgetStop):b.reserve('r','bull')
            self.assertEqual(b.read()['request_count'],1)

    def test_token_threshold_run_limit_and_persistent_request_caps(self):
        from integration.effect_cost import BatchBudget,BudgetStop
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.json'
            b=BatchBudget(path,max_requests=3,max_tokens=10,max_runs=2,per_run=1)
            b.start_run('a',{})
            rid=b.reserve('a','news');b.complete(rid,{'total_tokens':6},'ok')
            with self.assertRaises(BudgetStop):b.reserve('a','bull')
            b.start_run('b',{})
            rid=b.reserve('b','news');b.complete(rid,{'total_tokens':4},'ok')
            with self.assertRaises(BudgetStop):b.reserve('b','bull')
            with self.assertRaises(BudgetStop):b.start_run('c',{})
            self.assertEqual(BatchBudget(path).read()['total_tokens'],10)


class ContextTests(unittest.TestCase):
    def test_context_tables_and_repeated_text_are_lossless_including_negations(self):
        from integration.context_codec import pack_context,unpack_context
        text='Not a disclosed supply agreement. '+('Conditions, units 36GB 12H. '*30)
        view={'evidence':{'C_1':{'text':text,'source_id':'a','char_start':0,'char_end':len(text)},
                          'C_2':{'text':text,'source_id':'b','char_start':0,'char_end':len(text)}},
              'accepted_parents':[{'text':text,'conditions':['not all workloads'],'unknowns':['undisclosed']}],
              'untouched':{'from':None,'to':None}}
        encoded=pack_context(view)
        self.assertEqual(unpack_context(encoded),view)
        self.assertLess(len(json.dumps(encoded)),len(json.dumps(view)))
        self.assertIn('Not a disclosed supply agreement.',json.dumps(encoded))

    def test_pagination_reuses_original_query_budget_and_rejects_bad_cursor_as_data(self):
        from integration.runtime import EvidenceRuntime
        from integration.governance import new_shared_state
        rt=EvidenceRuntime('MU','HBM4','2026-10-18T15:38:11+00:00',False,use_dense=False)
        rt.max_total_chars=40000
        state=new_shared_state(rt.analysis_time,False);state['evidence_packets']=[]
        shared,summary,first=rt.retrieve(state,'HBM4 36GB 48GB samples')
        self.assertTrue(first['pagination']['next_cursor'])
        shared['evidence_packets']=[first]
        rt.max_total_chars=sum(len(e.get('text','')) for eid,e in shared['evidence_registry'].items() if eid.startswith('C_'))+6000
        # The remaining aggregate budget changed; the signed page query must not.
        again,summary,packet=rt.retrieve(shared,'HBM4 36GB 48GB samples',cursor=first['pagination']['next_cursor'])
        self.assertIn(summary['status'],{'partial','supported','insufficient'})
        bad,summary,packet=rt.retrieve(shared,'different question',cursor=first['pagination']['next_cursor'])
        self.assertIsNone(packet)
        self.assertEqual(summary['reason'],'cursor_not_from_same_verified_query')

    def test_real_usable_parent_preserves_full_evidence_conditions_and_unknowns(self):
        from integration.governance import new_shared_state,register_packet,append_claim,eligible_claims
        from integration.effect_context import build_model_context,ContextBudget
        at='2026-01-01T00:00:00+00:00';text='Micron manufactures memory.'
        state=new_shared_state(at,False)
        register_packet(state,{'analysis_time':at,'temporal_mode':'current_as_collected',
            'citations':{'C_parent':{'citation_id':'C_parent','source_id':'s','source_sha256':'a'*64,
                'text':text,'char_start':0,'char_end':len(text),'round_trip_verified':True,
                'available_at':'2025-01-01T00:00:00Z'}},'unknowns':['Orders are undisclosed.']})
        claim={'claim_id':'trader_fact','text':text,'type':'original_fact','subject':'MU','product':'unspecified',
            'business_scope':'memory','evidence_ids':['C_parent'],'parent_claim_ids':[],
            'conditions':['Disclosed scope only.'],'unknowns':['Orders are undisclosed.'],
            'available_at':at,'valid_time':{'from':None,'to':None},'analysis_time':at,
            'impact_target':'business','horizon':'disclosure','status':'proposed'}
        self.assertEqual(append_claim(state,claim,'trader')['claim']['status'],'accepted')
        parents=eligible_claims(state)
        ctx=build_model_context(state,'conservative','MU','memory',at,False,parents)
        actual=json.dumps(ctx['messages'])
        for value in (text,'Disclosed scope only.','Orders are undisclosed.','trader_fact'):
            self.assertIn(value,actual)
        self.assertEqual(ctx['view']['accepted_parents'][0]['version'],1)
        state['evidence_registry'].clear()
        with self.assertRaises(ContextBudget):build_model_context(state,'conservative','MU','memory',at,False,parents)

    def test_paging_cursor_and_tool_failure_reach_the_actual_model_messages(self):
        from integration.effect_context import build_model_context
        state={'evidence_registry':{},'claim_ledger':{},'received_event':{},'unresolved_gaps':[],
               'review_issues':[],'tool_trace':[{'tool':'company_research','status':'partial',
                'next_cursor':'OPAQUE_PAGE_2','reason':'text_budget'}]}
        ctx=build_model_context(state,'news','MU','HBM','2026-01-01T00:00:00Z',False,[])
        actual=json.dumps(ctx['messages'])
        self.assertIn('OPAQUE_PAGE_2',actual)
        self.assertIn('text_budget',actual)

    def test_reused_keyword_index_keeps_sqlite_connections_with_owning_worker(self):
        from concurrent.futures import ThreadPoolExecutor
        from lexical import KeywordIndex
        rows=[{'chunk_id':'x','text':'NVIDIA memory suppliers','available_at':'2024-01-01T00:00:00Z','historical_eligible':True}]
        index=KeywordIndex(':memory:',rows)
        first=index.search('memory','2025-01-01T00:00:00Z')
        with ThreadPoolExecutor(max_workers=1) as pool:
            second=pool.submit(index.search,'memory','2025-01-01T00:00:00Z').result()
        self.assertEqual(first['hits'],second['hits'])
        self.assertEqual(first['eligible_corpus_fingerprint'],second['eligible_corpus_fingerprint'])

    def test_blank_contract_contains_no_prefilled_answer(self):
        from integration.effect_context import role_prompt
        p=role_prompt('news')
        self.assertNotIn('claim_template',p['text'])
        self.assertIn('evidence_ids',p['text'])
        self.assertIn('parent_claim_ids',p['text'])
        self.assertNotIn('36GB',p['text'])

    def test_parent_evidence_and_negation_are_never_prefix_cut(self):
        from integration.effect_context import build_model_context,ContextBudget
        text='NVIDIA does not disclose a supplier-specific HBM4 share. '+('complete scope. '*80)
        e={'citation_id':'C_x','source_id':'s','text':text,'available_at':'2025-01-01T00:00:00Z'}
        state={'evidence_registry':{'C_x':e},'claim_ledger':{},'received_event':{'citation_ids':['C_x']},
               'unresolved_gaps':[],'review_issues':[]}
        result=build_model_context(state,'news','NVDA','supply','2026-01-01T00:00:00Z',False,[],max_chars=18000)
        self.assertEqual(result['view']['evidence']['C_x']['text'],text)
        self.assertIn('does not disclose',json.dumps(result['messages']))
        with self.assertRaises(ContextBudget):
            build_model_context(state,'news','NVDA','supply','2026-01-01T00:00:00Z',False,[],max_chars=100)


if __name__=='__main__':unittest.main()
