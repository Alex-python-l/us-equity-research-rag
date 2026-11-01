from pathlib import Path
import copy,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from event_analysis import comparable_gap,event_windows,DailyEventMarket,EventEvidence

class EventTests(unittest.TestCase):
    def test_after_close_and_weekend_use_real_sessions(self):
        out=event_windows('2026-10-30T20:01:00Z','2026-11-10T23:00:00Z')
        self.assertEqual(out['t0'],'2026-11-02')
        self.assertEqual(out['windows'][0]['end'],'2026-11-02T21:00:00+00:00')
        self.assertEqual(len(out['pre_event_volume_sessions']),20)
        self.assertEqual(out['windows'][2]['end_session'],'2026-11-09')
    def test_intraday_and_unfinished_are_explicit(self):
        out=event_windows('2026-10-30T16:00:00Z','2026-10-30T17:00:00Z')
        self.assertTrue(out['windows'][0]['mixed_pre_and_post_event'])
        self.assertEqual(out['windows'][0]['status'],'window_incomplete')
        self.assertEqual(out['intraday_response'],'unknown')
    def test_baselines_require_matching_scope_before_first_disclosure(self):
        event=dict(event_id='e',ticker='NVDA',metric='revenue',fiscal_period='FY2026Q3',business_scope='company',
            unit='USD bn',currency='USD',accounting_basis='GAAP',value_kind='actual',value=12,
            first_public_at='2026-10-20T20:10:00Z',first_public_status='verified')
        # A forecast and actual comparison intentionally uses the same metric type;
        # value_kind denotes metric basis, not publication document classification.
        baseline={**event,'value':10,'baseline_kind':'issuer_guidance','forecaster':'issuer','review_status':'reviewed','reviewed_by':'human',
            'reviewed_at':'2026-10-21T00:00:00Z','published_at':'2026-10-01T00:00:00Z','acquired_at':'2026-10-02T00:00:00Z',
            'version_id':'v','url':'https://example.test/original','quote':'Synthetic original guidance'}
        good=comparable_gap(event,[baseline],'2026-10-22T00:00:00Z')
        self.assertEqual(good['status'],'comparable_difference')
        self.assertEqual(good['difference_vs_market_consensus'],'unknown')
        for change in ({'published_at':'2026-10-21T00:00:00Z'},{'fiscal_period':'FY2027Q1'},{'currency':'EUR'},{'value_kind':'order_intent'}):
            self.assertEqual(comparable_gap(event,[{**baseline,**change}],'2026-10-22T00:00:00Z')['status'],'unknown')
        withdrawal={**baseline,'withdrawn':True,'version_id':'withdrawn','published_at':'2026-10-19T00:00:00Z','acquired_at':'2026-10-19T01:00:00Z'}
        self.assertEqual(comparable_gap(event,[baseline,withdrawal],'2026-10-22T00:00:00Z')['status'],'unknown')
    def test_daily_arithmetic_does_not_invent_missing_benchmark_or_prior_vintage(self):
        class Provider:
            def fetch(self,ticker,*a,**kw):
                return dict(provider='synthetic',acquired_at='2026-10-30T20:01:00Z',bars=[
                    dict(date='2026-10-29',adjusted_close=100,volume=100),dict(date='2026-10-30',adjusted_close=110,volume=200)] if ticker=='NVDA' else [])
        request=dict(ticker='NVDA',anchor='2026-10-30T12:00:00Z',anchor_status='verified',windows=[0,1,5])
        result=DailyEventMarket(Provider())(request,as_of='2026-10-30T22:00:00Z')
        self.assertAlmostEqual(result['windows'][0]['return_pct'],10)
        self.assertIsNone(result['windows'][0]['relative_return_pct'])
        self.assertEqual(result['windows'][1]['status'],'window_incomplete')
        self.assertEqual(result['priced_in'],'unknown')
        self.assertEqual(DailyEventMarket(Provider())(request,as_of='2026-10-30T20:00:00Z')['status'],'unknown')
    def test_missing_first_public_time_never_uses_article_date(self):
        result=DailyEventMarket()(dict(ticker='NVDA',published_at='2026-10-20T00:00:00Z'))
        self.assertEqual(result['status'],'unknown')
    def test_user_evidence_requires_trusted_original(self):
        with tempfile.TemporaryDirectory() as td:
            registry=EventEvidence(Path(td)/'e.sqlite3')
            with self.assertRaises(ValueError):registry.append('e',dict(reviewed_by='human',first_public_at='2026-10-20T00:00:00Z'),source_resolver=lambda v:None)
