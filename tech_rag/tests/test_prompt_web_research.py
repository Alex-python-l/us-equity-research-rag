"""Synthetic Responses fixtures; no network or paid calls."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class PromptWebTests(unittest.TestCase):
    def test_only_treatment_gets_rag_prompt_and_no_yahoo_query_tools_are_exposed(self):
        from integration.prompt_web_research import make_case,native_request
        guide={'ticker':'NVDA','guide_id':'g','agent_prompt':'上游：核实先进封装产能。'}
        a=make_case('NVDA','2026-10-22T10:00:00Z','A',None)
        b=make_case('NVDA','2026-10-22T10:00:00Z','B',guide)
        self.assertNotIn(guide['agent_prompt'],a['prompt']);self.assertIn(guide['agent_prompt'],b['prompt'])
        self.assertEqual(native_request(a)['instructions'],native_request(b)['instructions'])
        self.assertEqual(native_request(b)['tools'],[{'type':'web_search'}])
        self.assertNotIn('query_en',b['prompt']);self.assertNotIn('function',json.dumps(native_request(b)['tools']))

    def test_search_execution_and_source_links_required_and_prose_never_is_a_search_result(self):
        from integration.prompt_web_research import parse_research
        result={'conclusion':'未知','findings':[{'claim':'Synthetic planned expansion','source_urls':['https://issuer.example/a'],'kind':'reported_fact'}],'unknowns':['shipment']}
        raw={'model':'deepseek-v4-pro','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}
        self.assertFalse(parse_research(raw)['native_search_executed'])
        raw['output'].insert(0,{'type':'web_search_call','status':'completed','action':{'sources':[{'url':'https://issuer.example/a'}]}})
        out=parse_research(raw);self.assertTrue(out['native_search_executed']);self.assertEqual(len(out['findings']),1)
        result['findings'][0]['source_urls']=['https://invented.example/b']
        raw['output'][-1]['content'][0]['text']=json.dumps(result)
        self.assertEqual(parse_research(raw)['findings'],[])

    def test_explicit_risk_authorization_preserves_prior_unknown_and_new_unknown_stops(self):
        from integration.prompt_web_research import ExplicitABBudget
        from integration.effect_cost import BatchBudget,BudgetStop
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ledger.json';base=BatchBudget(path,max_runs=9,per_run=4,max_requests=24,max_tokens=120000)
            base.start_run('old',{});rid=base.reserve('old','old');base.complete(rid,None,'transport_failure_usage_unknown')
            base.finish_run('old','failed')
            auth=ExplicitABBudget.authorize(path,authorization_id='TEST_EXPLICIT',user_statement='explicit test authorization',case_ids=['NVDA_A','NVDA_B','MU_A','MU_B'])
            ledger=ExplicitABBudget(path,auth)
            ledger.start_run('TEST_EXPLICIT_NVDA_A_native',{})
            new=ledger.reserve('TEST_EXPLICIT_NVDA_A_native','native_prompt_research')
            ledger.complete(new,None,'transport_failure_usage_unknown')
            self.assertTrue(ledger.read()['usage_unknown']);self.assertIsNone(ledger.read()['requests'][0]['usage'])
            with self.assertRaises(BudgetStop):ledger.start_run('TEST_EXPLICIT_NVDA_B_native',{})
            with self.assertRaises(BudgetStop):BatchBudget(path).start_run('normal',{})

    def test_risk_authorization_cannot_be_recreated_or_exceeded_by_a_second_native_call(self):
        from integration.prompt_web_research import ExplicitABBudget
        from integration.effect_cost import BatchBudget,BudgetStop
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ledger.json';base=BatchBudget(path,max_runs=9,per_run=4,max_requests=24,max_tokens=120000)
            auth=ExplicitABBudget.authorize(path,authorization_id='TEST_LIMIT',user_statement='explicit test authorization',case_ids=['NVDA_A'])
            with self.assertRaises(BudgetStop):ExplicitABBudget.authorize(path,authorization_id='TEST_LIMIT',user_statement='again',case_ids=['NVDA_A'])
            b=ExplicitABBudget(path,auth);b.start_run('TEST_LIMIT_NVDA_A_native',{})
            r=b.reserve('TEST_LIMIT_NVDA_A_native','native_prompt_research');b.complete(r,{'total_tokens':1},'ok')
            with self.assertRaises(BudgetStop):b.reserve('TEST_LIMIT_NVDA_A_native','native_prompt_research')

    def test_single_run_rejects_missing_paid_consent_before_provider(self):
        from integration.prompt_web_research import run_prompt_research
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):run_prompt_research('NVDA',guide={'ticker':'NVDA','agent_prompt':'查封装','guide_id':'g'},directory=td,paid_confirmed=False)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_native_does_not_truncate_research_with_a_small_output_token_cap(self):
        from integration.prompt_web_research import native_request,make_case
        request=native_request(make_case('MU','2026-10-22T10:00:00Z','A'))
        self.assertNotIn('max_output_tokens',request)

    def test_successful_native_open_is_traceable_but_failed_open_is_not(self):
        from integration.prompt_web_research import parse_research
        report={'conclusion':'待核实','findings':[{'claim':'发行人公告线索','source_urls':['https://issuer.example/news/']}]}
        raw={'model':'deepseek-v4-pro','status':'completed','output':[
            {'type':'web_search_call','status':'completed','action':{'type':'search','queries':['issuer']}},
            {'type':'web_search_call','status':'completed','action':{'type':'open_page','url':'https://issuer.example/news/?ncid=tracking#ws_call_id=abc'}},
            {'type':'message','content':[{'type':'output_text','annotations':[],'text':json.dumps(report)}]}]}
        found=parse_research(raw)['findings']
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['verification_status'],'reported_unverified')
        self.assertEqual(found[0]['source_provenance'],'completed_native_open_or_returned_reference')
        raw['output'][1]['status']='failed'
        self.assertEqual(parse_research(raw)['findings'],[])

    def test_explicit_no_token_limit_is_scoped_and_preserves_original_ledger(self):
        from integration.prompt_web_research import ExplicitABBudget
        from integration.effect_cost import BatchBudget,BudgetStop
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ledger.json';base=BatchBudget(path,max_runs=1,max_tokens=1)
            base.start_run('old',{});r=base.reserve('old','old');base.complete(r,{'total_tokens':5},'ok');base.finish_run('old','done')
            auth=ExplicitABBudget.authorize(path,authorization_id='NO_TOKEN',user_statement='token用量预算不要有，真实检测',case_ids=['MU_A'],token_limit_disabled=True)
            b=ExplicitABBudget(path,auth);b.start_run('NO_TOKEN_MU_A_native',{})
            rid=b.reserve('NO_TOKEN_MU_A_native','native_prompt_research');b.complete(rid,{'total_tokens':500},'ok')
            b.start_run('NO_TOKEN_MU_A_discussion',{})
            self.assertEqual(b.read()['limits']['max_tokens'],1)
            self.assertEqual(b.read()['total_tokens'],505)
            with self.assertRaises(BudgetStop):base.start_run('normal',{})

if __name__=='__main__':unittest.main()
