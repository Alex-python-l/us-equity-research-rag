import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class APITests(unittest.TestCase):
    def service(self,td):
        from monitor_api import MonitorAPI
        api=MonitorAPI(state_dir=td)
        api.dependencies=lambda:{'calendar':{'ready':True},'news':{'ready':True},'agent':{'ready':True},'market':{'ready':False,'status':'pending_integration'}}
        return api

    def test_guide_draft_preview_do_not_enable_and_confirmation_reuses_plan(self):
        with tempfile.TemporaryDirectory() as td:
            api=self.service(td)
            guide=api.post('/api/monitor/guide',{'ticker':'NVDA'})
            self.assertNotIn('sources',guide);self.assertNotIn('http',guide['agent_prompt'])
            config=api.get('/api/monitor/status',{})['defaults']
            config['ticker']='NVDA'
            draft=api.post('/api/monitor/draft',{'config':config,'guide_id':guide['guide_id']})
            self.assertFalse(any(p['status']=='active' for p in api.get('/api/monitor/plans',{})['plans']))
            preview=api.post('/api/monitor/preview',{'draft_id':draft['draft_id']})
            self.assertEqual(len(preview['next_runs']),3);self.assertTrue(preview['topics'])
            plan=api.post('/api/monitor/confirm',{'draft_id':draft['draft_id'],'confirmation_hash':preview['confirmation_hash']})
            again=api.post('/api/monitor/confirm',{'draft_id':draft['draft_id'],'confirmation_hash':preview['confirmation_hash']})
            self.assertEqual(plan['plan_id'],again['plan_id'])
            self.assertEqual(plan['status'],'active')
            self.assertEqual(api.post('/api/monitor/action',{'plan_id':plan['plan_id'],'action':'pause'})['status'],'paused')

    def test_dependency_failure_blocks_confirmation_and_stale_payload_never_enables(self):
        with tempfile.TemporaryDirectory() as td:
            api=self.service(td);guide=api.post('/api/monitor/guide',{'ticker':'MU'})
            config=api.get('/api/monitor/status',{})['defaults'];config['ticker']='MU'
            draft=api.post('/api/monitor/draft',{'config':config,'guide_id':guide['guide_id']})
            preview=api.post('/api/monitor/preview',{'draft_id':draft['draft_id']})
            with self.assertRaises(ValueError):api.post('/api/monitor/confirm',{'draft_id':draft['draft_id'],'confirmation_hash':'stale'})
            api.dependencies=lambda:{'calendar':{'ready':True},'news':{'ready':False,'status':'pending_integration'},'agent':{'ready':True}}
            with self.assertRaises(ValueError):api.post('/api/monitor/confirm',{'draft_id':draft['draft_id'],'confirmation_hash':preview['confirmation_hash']})

    def test_guide_for_other_stock_cannot_be_attached_to_plan(self):
        with tempfile.TemporaryDirectory() as td:
            api=self.service(td);guide=api.post('/api/monitor/guide',{'ticker':'MU'})
            config=api.get('/api/monitor/status',{})['defaults'];config['ticker']='NVDA'
            with self.assertRaises(ValueError):api.post('/api/monitor/draft',{'config':config,'guide_id':guide['guide_id']})

    def test_confirmed_task_survives_api_reconstruction_without_webpage(self):
        with tempfile.TemporaryDirectory() as td:
            api=self.service(td);guide=api.post('/api/monitor/guide',{'ticker':'NVDA'})
            config=api.get('/api/monitor/status',{})['defaults']
            draft=api.post('/api/monitor/draft',{'config':config,'guide_id':guide['guide_id']})
            summary=api.post('/api/monitor/preview',{'draft_id':draft['draft_id']})
            plan=api.post('/api/monitor/confirm',{'draft_id':draft['draft_id'],'confirmation_hash':summary['confirmation_hash']})
            restored=self.service(td)
            self.assertEqual(restored.get('/api/monitor/plans',{})['plans'][0]['plan_id'],plan['plan_id'])

    def test_monitor_routes_keep_csrf_gate_and_serve_actual_saved_guide(self):
        from chat_server import make_handler
        from http.server import ThreadingHTTPServer
        import http.client,threading
        with tempfile.TemporaryDirectory() as td:
            api=self.service(td)
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(None,'local-test',api))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=http.client.HTTPConnection('127.0.0.1',server.server_port)
                client.request('POST','/api/monitor/guide',json.dumps({'ticker':'NVDA'}),{'Content-Type':'application/json'})
                response=client.getresponse();response.read();self.assertEqual(response.status,403)
                client.request('POST','/api/monitor/guide',json.dumps({'ticker':'NVDA'}),{'Content-Type':'application/json','X-CSRF-Token':'local-test'})
                response=client.getresponse();guide=json.loads(response.read());self.assertEqual(response.status,200)
                client.request('GET','/api/monitor/guide?id='+guide['guide_id'])
                response=client.getresponse();audit=json.loads(response.read());self.assertTrue(audit['sources'])
                self.assertEqual(audit['guide']['guide_id'],guide['guide_id']);client.close()
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
