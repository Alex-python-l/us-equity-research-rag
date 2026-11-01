"""Native server-side search evidence contracts; no fixture is real news."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def response(search=True, status='completed', url='https://issuer.example/original'):
    items=[]
    if search:items.append({'type':'web_search_call','status':'completed','action':{'type':'search',
        'query':'capacity','sources':[{'url':url,'title':'Original'}]}})
    items.append({'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps(
        {'events':[{'event':'announced a plan','url':url,'source':'Issuer','published_at':'2026-10-18T00:00:00Z',
          'event_time':None,'industry_stage':'capacity','conditions':['execution'],'unknowns':['orders']}]})}]})
    return {'model':'deepseek-v4-pro','status':status,'output':items,'usage':{'input_tokens':100,'output_tokens':50,'total_tokens':150}}


class NativeSearchTests(unittest.TestCase):
    def mod(self):
        from integration import native_search
        return native_search

    def test_model_prose_without_native_execution_cannot_be_browsed_news(self):
        out=self.mod().parse_native_response(response(False),'2026-10-20T00:00:00Z')
        self.assertFalse(out['native_search_executed']);self.assertEqual(out['items'],[])

    def test_native_candidates_stay_unverified_and_keep_search_proof(self):
        out=self.mod().parse_native_response(response(),'2026-10-20T00:00:00Z')
        self.assertTrue(out['native_search_executed']);self.assertEqual(out['visible_server_search_calls'],1)
        self.assertFalse(out['items'][0]['source_verified']);self.assertIsNone(out['items'][0]['actual_public_at'])
        self.assertEqual(out['items'][0]['url'],'https://issuer.example/original')

    def test_urls_not_in_tool_evidence_and_incomplete_output_are_rejected(self):
        raw=response();raw['output'][-1]['content'][0]['text']=json.dumps({'events':[{'url':'https://invented.example/a'}]})
        self.assertEqual(self.mod().parse_native_response(raw,'2026-10-20T00:00:00Z')['items'],[])
        self.assertEqual(self.mod().parse_native_response(response(status='incomplete'),'2026-10-20T00:00:00Z')['items'],[])

    def test_future_candidate_is_excluded_and_no_safe_endpoint_leaks(self):
        raw=response();raw['output'][-1]['content'][0]['text']=json.dumps({'events':[{'url':'https://issuer.example/original','published_at':'2026-10-21T00:00:00Z'}]})
        self.assertEqual(self.mod().parse_native_response(raw,'2026-10-20T00:00:00Z')['items'],[])
        raw=response(url='http://127.0.0.1/admin')
        self.assertEqual(self.mod().parse_native_response(raw,'2026-10-20T00:00:00Z')['items'],[])

    def test_historical_gate_precedes_provider_and_credential_loading(self):
        with tempfile.TemporaryDirectory() as td,patch('integration.secure_config.load_llm_config',side_effect=AssertionError('secret read')):
            out=self.mod().discover_native({'historical':True},directory=td,config_path='ignored')
        self.assertEqual(out['status'],'unsupported_historical')

    def test_request_targets_provider_builtin_only_and_exposes_no_function(self):
        m=self.mod()
        p={'ticker':'MU','question':'capacity','analysis_time':'2026-10-20T00:00:00Z','historical':False,
           'industry_context':None,'search_tasks':[]}
        req=m.native_payload(p)
        self.assertEqual(req['model'],'deepseek-v4-pro');self.assertEqual(req['tools'],[{'type':'web_search'}])
        self.assertNotIn('max_tool_calls',req);self.assertNotIn('function',json.dumps(req['tools']))

    def test_transport_failure_records_unknown_usage_and_blocks_followups(self):
        from integration.effect_cost import BatchBudget,BudgetStop
        from integration.secure_config import LLMConfig
        import requests
        m=self.mod();p={'ticker':'MU','question':'capacity','analysis_time':'2026-10-20T00:00:00Z',
            'historical':False,'industry_context':None,'search_tasks':[]}
        with tempfile.TemporaryDirectory() as td:
            ledger=BatchBudget(Path(td)/'ledger.json',max_runs=2,per_run=4,max_requests=4,max_tokens=120000)
            config=LLMConfig('deepseek','deepseek-flash','https://api.deepseek.com','fake-test-key-do-not-use')
            with patch('integration.secure_config.load_llm_config',return_value=config),patch('requests.post',side_effect=requests.ConnectionError()):
                out=m.discover_native(p,directory=Path(td)/'discovery',config_path='fake',budget_ledger=ledger,run_id='one')
            self.assertEqual(out['status'],'transport_or_format_failure');self.assertTrue(ledger.read()['usage_unknown'])
            self.assertEqual(ledger.read()['requests'][0]['status'],'transport_failure_usage_unknown')
            with self.assertRaises(BudgetStop):ledger.start_run('two',{})

    def test_wrong_model_response_is_not_native_success(self):
        raw=response();raw['model']='deepseek-flash'
        self.assertFalse(self.mod().parse_native_response(raw,'2026-10-20T00:00:00Z')['native_search_executed'])

    def test_query_URL_echo_is_not_a_provider_returned_source(self):
        raw=response();url='https://issuer.example/original'
        raw['output'][0]['action']={'query':'site:'+url,'sources':[]}
        self.assertEqual(self.mod().parse_native_response(raw,'2026-10-20T00:00:00Z')['items'],[])


if __name__=='__main__':unittest.main()
