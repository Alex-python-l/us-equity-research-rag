import sys,json,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ResearchMemoryTests(unittest.TestCase):
    def test_append_idempotence_and_no_future_review_injection(self):
        from integration.research_memory import ResearchMemory
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'memory.sqlite3')
            first=m.record('r1','NVDA','B','2026-10-19T15:00:00Z',{'final_conclusion':'待核交付'},namespace='live',recorded_at='2026-10-19T15:05:00Z')
            self.assertEqual(first,m.record('r1','NVDA','B','2026-10-19T15:00:00Z',{'final_conclusion':'待核交付'},namespace='live',recorded_at='2026-10-19T15:05:00Z'))
            with self.assertRaises(ValueError):m.record('r1','NVDA','B','2026-10-19T15:00:00Z',{'final_conclusion':'改写'},namespace='live')
            obs=[{'url':'https://issuer.example/news','published_at':'2026-10-20T10:00:00Z','acquired_at':'2026-10-20T11:00:00Z','statement':'新增公告'}]
            m.review(first,'2026-10-20T12:00:00Z',obs,'仍须确认实际交付',recorded_at='2026-10-21T00:00:00Z')
            self.assertEqual(m.context('NVDA','2026-10-20T13:00:00Z',namespace='live'),[])
            got=m.context('NVDA','2026-10-22T00:00:00Z',namespace='live')
            self.assertEqual(len(got),1);self.assertFalse(got[0]['fact_promotion_allowed'])
            self.assertEqual(m.context('NVDA','2026-10-22T00:00:00Z',namespace='other'),[])

    def test_future_or_missing_public_acquisition_time_rejected(self):
        from integration.research_memory import ResearchMemory
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');key=m.record('r','MU','A','2026-10-19T00:00:00Z',{'final_conclusion':'未知'},recorded_at='2026-10-19T00:01:00Z')
            for obs in [[],[{'url':'https://example.test','published_at':'2026-10-22T00:00:00Z','acquired_at':'2026-10-20T00:00:00Z'}],[{'url':'https://example.test','published_at':'2026-10-19T00:00:00Z'}]]:
                with self.assertRaises(ValueError):m.review(key,'2026-10-20T00:00:00Z',obs,'test')

    def test_arms_isolated_and_review_versions_not_overwritten(self):
        from integration.research_memory import ResearchMemory
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');key=m.record('r','MU','A','2026-10-19T00:00:00Z',{'final_conclusion':'未知'},recorded_at='2026-10-19T00:01:00Z')
            obs=[{'url':'https://issuer.example/a','published_at':'2026-10-20T00:00:00Z','acquired_at':'2026-10-20T01:00:00Z'}]
            a=m.review(key,'2026-10-20T02:00:00Z',obs,'first',recorded_at='2026-10-20T02:01:00Z')
            b=m.review(key,'2026-10-20T03:00:00Z',obs,'second',recorded_at='2026-10-20T03:01:00Z')
            self.assertNotEqual(a,b);self.assertEqual(len(m.reviews(key)),2)
            self.assertEqual(m.context('MU','2026-10-21T00:00:00Z',arm='B'),[])
            self.assertEqual(m.context('MU','2026-10-21T00:00:00Z',arm='A')[0]['lesson'],'second')

    def test_missing_price_data_stays_pending_and_no_premature_lesson(self):
        from integration.research_memory import ResearchMemory,settle_memory
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');key=m.record('r','MU','B','2026-10-19T15:00:00Z',{'final_conclusion':'未知'},recorded_at='2026-10-19T15:01:00Z')
            result=settle_memory(m,'2026-10-27T21:00:00Z',market_provider=lambda *a:None)
            self.assertEqual(result['completed'],0);self.assertEqual(m.reviews(key),[])

    def test_settled_observation_is_idempotent_and_never_proves_business_thesis(self):
        from integration.research_memory import ResearchMemory,settle_memory,followup_windows
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');key=m.record('r','MU','B','2026-10-19T15:00:00Z',{'final_conclusion':'未知'},recorded_at='2026-10-19T15:01:00Z')
            calls=[]
            def provider(ticker,window,at):
                calls.append(window['days'])
                return {'raw_return_pct':2,'benchmark_return_pct':1,'relative_return_pct':1,
                    'published_at':window['due_at'],'acquired_at':'2026-10-27T21:00:00Z','url':'https://finance.yahoo.com/quote/MU/history/'}
            from unittest.mock import patch
            with patch('integration.research_memory.now',return_value='2026-10-27T21:00:01Z'):
                a=settle_memory(m,'2026-10-27T21:00:00Z',market_provider=provider)
                b=settle_memory(m,'2026-10-27T21:00:00Z',market_provider=provider)
            self.assertEqual(a['completed'],2);self.assertEqual(b['completed'],0);self.assertEqual(calls,[1,5])
            self.assertTrue(all(not r['payload']['fact_promotion_allowed'] for r in m.reviews(key)))

    def test_live_settlement_does_not_set_acquisition_deadline_before_fetch(self):
        from integration.research_memory import ResearchMemory,settle_memory
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');m.record('r','MU','B','2026-10-19T15:00:00Z',{'final_conclusion':'未知'},recorded_at='2026-10-19T15:01:00Z')
            cutoffs=[]
            def provider(ticker,window,cutoff):
                cutoffs.append(cutoff)
                return {'raw_return_pct':2,'benchmark_return_pct':1,'relative_return_pct':1,
                    'published_at':window['due_at'],'acquired_at':'2026-10-27T21:00:01Z','url':'https://finance.yahoo.com/quote/MU/history/'}
            with patch('integration.research_memory.now',return_value='2026-10-27T21:00:02Z'):
                result=settle_memory(m,market_provider=provider)
            self.assertEqual(cutoffs,[None,None]);self.assertEqual(result['completed'],2)

    def test_concurrent_record_is_idempotent(self):
        from integration.research_memory import ResearchMemory
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with tempfile.TemporaryDirectory() as td:
            m=ResearchMemory(Path(td)/'m.db');gate=threading.Barrier(6)
            def write(_):
                gate.wait()
                return m.record('same','NVDA','B','2026-10-19T15:00:00Z',{'final_conclusion':'same'})
            with ThreadPoolExecutor(max_workers=6) as pool:values=list(pool.map(write,range(6)))
            self.assertEqual(len(set(values)),1);self.assertEqual(len(m.conclusions()),1)

if __name__=='__main__':unittest.main()
