"""Runtime contracts use explicitly synthetic providers; no live calls."""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

NOW='2026-10-22T15:00:00+00:00'

class Planner:
    def get_guide(self,gid):
        return {'guide_id':gid,'ticker':'NVDA','version':1,'topics':[
            {'id':'supply','title':'供应','search_objects':['TSMC'],'keywords':['capacity'],
             'impact_path':'制造变化 → 交付变化','priority':'高'}]}
    def event_task(self,ticker,event,as_of=None):
        return {'task_id':'task-'+event['event_id'],'instructions':['核查首次披露'],'agent_prompt':'核查首次披露，比较事前预期。','status':'pending_verification'}

def job():
    return {'run_id':'r1','plan_id':'p1','kind':'search','scheduled_at':NOW,'guide_id':'g1','watermark':None,
      'config':{'ticker':'NVDA','market':'XNAS','retrieval':{'lookback_hours':72,'overlap_minutes':120},
       'budgets':{'requests_per_day':80,'llm_calls_per_day':6},'cadence':{'llm_min_minutes':30},
       'analysis':{'event':{'enabled':True,'trading_days':[0,1,5]},'fundamental':{'enabled':True,'cadences':['monthly']}},
       'followup':{'trading_days':[1,5],'after_disclosure':True},'sources':['yahoo']}}

def candidate(title='Synthetic capacity change'):
    return {'candidate_id':'synthetic-c1','title':title,'url':'https://issuer.example/a',
            'provider_published_at':'2026-10-20T12:00:00+00:00','first_seen_at':NOW}

class RuntimeTests(unittest.TestCase):
    def runtime(self,td,**kwargs):
        from monitor_runtime import MonitorRuntime
        return MonitorRuntime(Planner(),state_dir=td,**kwargs)

    def test_unfinished_body_is_resumed_even_when_new_search_omits_it(self):
        responses=[{'status':'candidates','items':[candidate()]},{'status':'no_candidates','items':[]}]
        bodies=[{'eligible':False},{'eligible':True,'body_acquired':True,'body_text':'Synthetic original body','version_id':'recovered'}]
        calls=[]
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:responses.pop(0),fetch=lambda *a,**k:bodies.pop(0),
                            agent=lambda **k:calls.append(1) or {'findings':[]})
            rt.execute(job(),now=NOW)
            out=rt.execute({**job(),'run_id':'r2'},now='2026-10-22T15:31:00+00:00')
            self.assertEqual(calls,[1]);self.assertEqual(len(rt.events()),1)
            self.assertEqual(rt.events()[0]['original_version'],'recovered')

    def test_no_new_skips_model_and_second_identical_round_is_deduplicated(self):
        calls=[]
        def search(*a,**kw):return {'status':'candidates','items':[candidate()],'network_calls':1}
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=search,fetch=lambda *a,**kw:{'eligible':False,'body_acquired':False},
                agent=lambda *a,**kw:calls.append(1))
            a=rt.execute(job(),now=NOW)
            b=rt.execute({**job(),'run_id':'r2'},now='2026-10-22T15:31:00+00:00')
            self.assertEqual(a['new_items'],1);self.assertEqual(b['status'],'no_new')
            self.assertEqual(calls,[]);self.assertEqual(b['llm_calls'],0)
            self.assertEqual(len(rt.events()),1)

    def test_provider_failure_is_not_no_new_and_returns_no_success_watermark(self):
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'provider_unavailable','items':[]})
            result=rt.execute(job(),now=NOW)
            self.assertEqual(result['status'],'failed');self.assertIsNone(result.get('watermark'))

    def test_provider_failure_does_not_poison_cache_for_retry(self):
        responses=[{'status':'provider_unavailable','items':[]},{'status':'no_candidates','items':[]}]
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:responses.pop(0))
            self.assertEqual(rt.execute(job(),now=NOW)['status'],'failed')
            self.assertEqual(rt.execute({**job(),'run_id':'r2'},now=NOW)['status'],'no_new')

    def test_delayed_discovery_keeps_publication_and_first_seen_distinct_with_unknown_assessments(self):
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[candidate()]},
                fetch=lambda *a,**k:{'eligible':False})
            rt.execute(job(),now=NOW)
            event=rt.events()[0]
            self.assertEqual(event['provider_published_at'],'2026-10-20T12:00:00+00:00')
            self.assertEqual(event['first_seen_at'],NOW)
            self.assertEqual(event['assessment']['expectation_gap']['status'],'unknown')
            self.assertEqual(event['assessment']['market_reaction']['status'],'unknown')
            self.assertEqual(event['classification'],'insufficient_information')
            self.assertNotIn('verified',event['task']['status'])

    def test_changed_title_creates_new_version_without_overwriting_previous_judgment(self):
        responses=[candidate(),candidate('Synthetic capacity update with new detail')]
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[responses.pop(0)]},fetch=lambda *a,**k:{'eligible':False})
            rt.execute(job(),now=NOW)
            rt.execute({**job(),'run_id':'r2'},now='2026-10-22T15:31:00+00:00')
            self.assertEqual(len(rt.events()),2)
            self.assertEqual(rt.events(as_of=NOW)[0]['judged_at'],NOW)

    def test_request_budget_stops_before_external_call(self):
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:self.fail('over-budget request'))
            j=job();j['config']['budgets']['requests_per_day']=1
            self.assertEqual(rt.execute(j,now=NOW)['status'],'budget_exhausted')

    def test_incremental_window_uses_last_success_with_overlap_not_execution_frequency(self):
        calls=[]
        def search(task,*a,**kw):calls.append(task);return {'status':'no_candidates','items':[]}
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=search);j=job();j['watermark']='2026-10-22T14:00:00+00:00'
            out=rt.execute(j,now=NOW)
            self.assertEqual(calls[0]['time_window']['start'],'2026-10-22T12:00:00+00:00')
            self.assertEqual(out['watermark'],NOW)

    def test_historical_view_requires_public_and_system_available_times(self):
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[candidate()]},fetch=lambda *a,**k:{'eligible':False})
            rt.execute(job(),now=NOW)
            self.assertEqual(rt.events(as_of='2026-10-21T00:00:00+00:00'),[])
            self.assertEqual(len(rt.events(as_of=NOW)),1)

    def test_price_metrics_are_calculated_and_missing_benchmark_is_unknown(self):
        from monitor_runtime import price_metrics
        r=price_metrics([{'close':100,'volume':10},{'close':110,'volume':20}],[])
        self.assertAlmostEqual(r['price_return_pct'],10)
        self.assertEqual(r['volume_ratio'],2)
        self.assertIsNone(r['relative_return_pct'])
        self.assertEqual(r['causal_attribution'],'unknown')

    def test_agent_failure_keeps_discovery_and_task_without_success_checkpoint(self):
        def fail(**kw):raise RuntimeError('synthetic failure')
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[candidate()]},
                fetch=lambda *a,**k:{'eligible':True,'body_acquired':True,'body_text':'Synthetic original body','version_id':'v1'},agent=fail)
            out=rt.execute(job(),now=NOW)
            self.assertEqual(out['status'],'failed');self.assertIsNone(out['watermark'])
            self.assertEqual(len(rt.events()),1)
            self.assertEqual(rt.events()[0]['agent_status'],'agent_error')

    def test_followup_uses_newly_verified_time_without_overwriting_old_judgment(self):
        seen=[]
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[candidate()]},fetch=lambda *a,**k:{'eligible':False})
            rt.execute(job(),now=NOW);event=rt.events()[0]
            rt._fetch=lambda *a,**k:{'eligible':True,'body_acquired':True,'published_at':'2026-10-20T12:00:00+00:00','version_id':'v2','status':'body_verified'}
            rt.market=lambda request,**k:seen.append(request['anchor']) or {'status':'unknown'}
            out=rt.execute({**job(),'kind':'event_followup','event_id':event['event_id']},now='2026-10-23T15:00:00+00:00')
            self.assertEqual(out['status'],'success')
            # A newly verified article timestamp does not prove earliest disclosure.
            self.assertEqual(seen,[None])
            self.assertEqual(rt.events()[0]['original_version'],'v2')
            self.assertIsNone(rt.events(as_of=NOW)[0]['published_at'])

    def test_rotated_theme_keeps_its_own_success_position(self):
        class Many(Planner):
            def get_guide(self,gid):
                g=super().get_guide(gid)
                g['topics']=[{**g['topics'][0],'id':str(i),'keywords':['capacity',str(i)]} for i in range(6)]
                return g
        requests=[]
        def search(task,*a,**k):requests.append(task);return {'status':'no_candidates','items':[]}
        from monitor_runtime import MonitorRuntime
        with tempfile.TemporaryDirectory() as td:
            rt=MonitorRuntime(Many(),state_dir=td,search=search)
            j=job();j['config']['budgets']['requests_per_day']=200
            first=rt.execute(j,now=NOW);first_task=requests[0]['task_id']
            j2={**job(),'run_id':'r2','scheduled_at':'2026-10-22T17:00:00+00:00','watermark':NOW}
            j2['config']['budgets']['requests_per_day']=200
            rt.execute(j2,now=j2['scheduled_at'])
            new_theme=next(t for t in requests[2:] if t['task_id']!=first_task)
            self.assertEqual(new_theme['time_window']['start'],'2026-10-19T17:00:00+00:00')

    def test_pause_during_search_stops_following_external_body_and_agent_calls(self):
        class Store:
            status='active'
            def get_plan(self,pid):return {'status':self.status}
        store=Store()
        def search(*a,**k):store.status='paused';return {'status':'candidates','items':[candidate()]}
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,store=store,search=search,fetch=lambda *a,**k:self.fail('body fetched after pause'),agent=lambda **k:self.fail('agent called after pause'))
            out=rt.execute(job(),now=NOW)
            self.assertEqual(out['status'],'blocked');self.assertIsNone(out['watermark'])
            self.assertEqual(len(rt.events()),1)

    def test_verified_issuer_disclosure_enqueues_reviews_without_promoting_old_event_facts(self):
        class Store:
            queued=[]
            def get_plan(self,pid):return {'status':'active'}
            def enqueue_followup(self,pid,event_id,at):self.queued.append((event_id,at))
        from monitor_schedule import default_config
        store=Store();j=job();j['config']=default_config('NVDA',now=NOW)
        with tempfile.TemporaryDirectory() as td:
            rt=self.runtime(td,search=lambda *a,**k:{'status':'candidates','items':[candidate()]},fetch=lambda *a,**k:{'eligible':False})
            rt.execute(j,now=NOW)
            old=rt.events()[0]['event_id'];rt.store=store
            rt._search=lambda *a,**k:{'status':'candidates','items':[{'title':'NVIDIA financial results','url':'https://investor.nvidia.com/earnings/q3','provider_published_at':'2026-10-23T14:00:00+00:00'}]}
            rt._fetch=lambda *a,**k:{'eligible':True,'body_acquired':True,'published_at':'2026-10-23T14:00:00+00:00','status':'body_verified','version_id':'official1'}
            out=rt.execute({**j,'run_id':'r2','scheduled_at':'2026-10-23T15:00:00+00:00'},now='2026-10-23T15:00:00+00:00')
            self.assertEqual(out['status'],'success')
            self.assertTrue(any(eid==old for eid,at in store.queued))
            self.assertEqual(next(e for e in rt.events() if e['event_id']==old)['assessment']['expectation_gap']['status'],'unknown')

    def test_backend_worker_processes_confirmed_persistent_job_without_browser(self):
        import threading
        from datetime import datetime,timezone,timedelta
        from monitor_store import MonitorStore
        from monitor_schedule import default_config
        from monitor_runtime import MonitorRuntime
        called=threading.Event();now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            store=MonitorStore(Path(td)/'plans.db');config=default_config('NVDA',now=now-timedelta(hours=1))
            ready={k:{'ready':True} for k in ['calendar','news','agent']}
            draft=store.save_draft(config,'g1');preview=store.preview(draft['draft_id'],ready,now=now)
            plan=store.confirm(draft['draft_id'],preview['confirmation_hash'],ready,now=now)
            with store._db(write=True) as db:
                import json
                db.execute('UPDATE monitor_plans SET next_due=? WHERE plan_id=?',
                    (json.dumps({'search':(now-timedelta(seconds=1)).isoformat()}),plan['plan_id']))
            def search(*a,**k):called.set();return {'status':'no_candidates','items':[]}
            rt=MonitorRuntime(Planner(),state_dir=td,store=store,search=search,agent=lambda **k:self.fail('no-new LLM'))
            try:rt.start();self.assertTrue(called.wait(5))
            finally:rt.stop();rt.thread.join(timeout=3)
            runs=store.list_runs()
            self.assertEqual(len(runs),1)
            self.assertEqual(runs[0]['status'],'success')
            self.assertEqual(runs[0]['result']['status'],'no_new')
            self.assertEqual(runs[0]['result']['llm_calls'],0)

if __name__=='__main__':unittest.main()
