"""Production dual-route ordering, not simulated LLM quality."""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class DualNewsTests(unittest.TestCase):
    def mod(self):
        from integration import dual_news
        return dual_news

    def test_history_has_no_Yahoo_or_native_provider_calls(self):
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            out=m.prepare_dual_research('MU','HBM','2020-01-01T00:00:00Z',directory=td,
                use_dense=False,company_search=lambda *a,**kw:self.fail('Yahoo called'),
                native_discover=lambda *a,**kw:self.fail('native called'))
        self.assertEqual(out['news_mode'],'native');self.assertEqual(out['native_discovery']['status'],'unsupported_historical')

    def test_route_merge_is_round_robin_and_duplicate_URLs_retain_provenance(self):
        m=self.mod()
        direct=[{'url':'https://a.example/one','title':'one'}]
        industry=[{'url':'https://a.example/one','title':'same'},{'url':'https://b.example/two','title':'two'}]
        rows=m.merge_route_candidates(direct,industry)
        self.assertEqual([r['url'] for r in rows],['https://a.example/one','https://b.example/two'])
        self.assertEqual(rows[0]['discovery_routes'],['company_yahoo','industry_native'])

    def test_pending_cost_stops_normal_entry_before_another_request(self):
        from integration.effect_cost import BatchBudget,BudgetStop
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            ledger=BatchBudget(Path(td)/'ledger.json');ledger.start_run('previous',{});ledger.reserve('previous','native')
            with patch('integration.dual_news.prepare_dual_research',side_effect=AssertionError('must stop before search')):
                with self.assertRaises(BudgetStop):m.run_dual_research('MU','x',model_mode='online',budget_ledger=ledger,archive_dir=Path(td)/'run')

    def test_current_background_cannot_be_reused_as_historical_before_network(self):
        from integration.industry_pipeline import prepare_research
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            p=prepare_research('MU','HBM','current',use_dense=False,fetch_news=False,archive_dir=Path(td)/'base')
            with self.assertRaises(ValueError):
                m.prepare_dual_research('MU','HBM','2020-01-01T00:00:00Z',directory=Path(td)/'run',base_prepared=p,
                    company_search=lambda *a,**kw:self.fail('Yahoo called'),native_discover=lambda *a,**kw:self.fail('native called'))

    def test_native_entry_rejects_external_search_instead_of_silently_ignoring_it(self):
        from integration.framework import run_research
        with patch('integration.dual_news.run_dual_research',side_effect=AssertionError('should reject before dispatch')):
            with self.assertRaises(ValueError):run_research('MU','x',model_mode='online',search_provider=lambda *a:None)

    def test_one_query_budget_fails_before_any_network(self):
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):m.prepare_dual_research('MU','HBM','current',directory=td,
                use_dense=False,budget={'max_queries':1},company_search=lambda *a,**kw:self.fail('Yahoo called'),
                native_discover=lambda *a,**kw:self.fail('native called'))

    def test_shorter_lookback_is_propagated_before_discovery(self):
        m=self.mod();seen=[]
        def company(*a,**kwargs):seen.append(kwargs['lookback_days']);return {'items':[],'status':'none'}
        def native(p,**kwargs):
            seen.append(p['budget']['lookback_days']);return {'items':[],'status':'none','native_search_executed':False,'model_requests':0}
        with tempfile.TemporaryDirectory() as td:
            p=m.prepare_dual_research('MU','HBM','current',directory=td,use_dense=False,
                budget={'lookback_days':7},company_search=company,native_discover=native)
        self.assertEqual(seen,[7,7]);self.assertEqual(p['budget']['lookback_days'],7)

    def test_unexecuted_native_snapshot_cannot_supply_industry_candidates(self):
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            p=m.prepare_dual_research('MU','HBM','current',directory=td,use_dense=False,
                company_snapshot={'items':[],'status':'none'},native_snapshot={
                    'native_search_executed':False,'status':'failed','items':[{'url':'https://example.com/fake','title':'claim'}]},
                article_provider=lambda *a,**kw:self.fail('unexecuted search candidate must not be fetched'))
        self.assertEqual(p['news_candidates'],[])

    def test_frozen_background_restrictive_budget_is_reused_without_restating_it(self):
        from integration.industry_pipeline import prepare_research
        m=self.mod()
        with tempfile.TemporaryDirectory() as td:
            base=prepare_research('MU','HBM','current',use_dense=False,fetch_news=False,archive_dir=Path(td)/'base',
                budget={'lookback_days':7,'max_body_fetches':1,'results_per_query':1})
            p=m.prepare_dual_research('MU','HBM','current',directory=Path(td)/'run',base_prepared=base,
                company_snapshot={'items':[],'status':'none'},native_snapshot={'native_search_executed':False,'items':[]})
        self.assertEqual(p['budget'],base['budget'])

    def test_normal_entry_uses_native_for_online_and_legacy_for_explicit_baseline(self):
        from integration.framework import run_research
        with patch('integration.dual_news.run_dual_research',return_value={'route':'native'}) as native:
            out=run_research('MU','x',model_mode='online',llm_config_path='authorized')
        self.assertEqual(out['route'],'native');native.assert_called_once()
        with patch('integration.industry_pipeline.run_industry_research',return_value={'route':'legacy'}) as legacy:
            out=run_research('MU','x',model_mode='online',news_mode='legacy')
        self.assertEqual(out['route'],'legacy');legacy.assert_called_once()


if __name__=='__main__':unittest.main()
