import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class USResearchTests(unittest.TestCase):
    def test_full_graph_has_twelve_roles_and_frozen_memory_without_auto_settlement(self):
        from integration.us_research import build_full_graph
        from langchain_core.runnables import Runnable
        class Offline(Runnable):
            def bind_tools(self,*a,**k):return self
            def with_structured_output(self,*a,**k):return self
            def invoke(self,*a,**k):raise AssertionError('No model calls allowed')
        with tempfile.TemporaryDirectory() as td:
            graph=build_full_graph(Path(td),model=Offline(),memory_snapshot=[{'lesson':'需核交付','fact_promotion_allowed':False}])
            self.assertTrue({'Market Analyst','Sentiment Analyst','News Analyst','Fundamentals Analyst','Portfolio Manager'}<=set(graph.graph.nodes))
            update=graph._memory_step({'company_of_interest':'NVDA','trade_date':'2026-10-27'})
            self.assertIn('需核交付',update['past_context'])
            self.assertIn('unverified',update['memory_note'])

    def test_paid_consent_and_historical_vintage_gates_precede_models(self):
        from integration.us_research import run_full_research
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):run_full_research('NVDA',directory=td,paid_confirmed=False)
            with self.assertRaises(ValueError):run_full_research('NVDA',directory=td,paid_confirmed=True,as_of='2025-01-01T00:00:00Z')
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_explicit_llm_file_reaches_framework_without_environment_secret(self):
        from integration.us_research import audited_model
        from unittest.mock import patch
        import os,socket
        with tempfile.TemporaryDirectory() as td:
            config=Path(td)/'key.env';config.write_text('DEEPSEEK_API_KEY=sk-SYNTHETICabcdefghijklmnop\n',encoding='utf-8')
            with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}),patch.object(socket.socket,'connect',side_effect=AssertionError('offline only')):
                model=audited_model(Path(td)/'calls',config)
                self.assertIsNotNone(model)
                self.assertEqual(os.environ['DEEPSEEK_API_KEY'],'')

    def test_optional_typesafe_key_never_starts_unbudgeted_screening(self):
        from integration.us_research import without_paid_screening
        from integration.bootstrap import bootstrap
        from unittest.mock import patch
        bootstrap()
        import tradingagents.agents.analysts.sentiment_analyst as sentiment
        with patch.object(sentiment,'jev_screen',side_effect=AssertionError('unbudgeted paid call')):
            def node(state):return {'screen':sentiment.jev_screen('NVDA')}
            self.assertIsNone(without_paid_screening(node)({})['screen'])

    def test_concurrent_social_guards_cannot_restore_paid_screener_early(self):
        from integration.us_research import without_paid_screening
        from integration.bootstrap import bootstrap
        from unittest.mock import patch
        from concurrent.futures import ThreadPoolExecutor
        import threading,time
        bootstrap()
        import tradingagents.agents.analysts.sentiment_analyst as sentiment
        entered=threading.Event();values=[]
        def first(state):
            entered.set();time.sleep(.05)
            return sentiment.jev_screen('NVDA')
        def second(state):
            time.sleep(.08)
            return sentiment.jev_screen('NVDA')
        with patch.object(sentiment,'jev_screen',return_value='ORIGINAL_PAID_SCREEN'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(without_paid_screening(first),{})
                self.assertTrue(entered.wait(2))
                b=pool.submit(without_paid_screening(second),{})
                values=[a.result(),b.result()]
        self.assertEqual(values,[None,None])

    def test_default_four_case_ab_budget_blocked_before_provider(self):
        from integration.us_research import preflight_models
        ledger={'request_count':4,'total_tokens':15782,'usage_unknown':False,'requests':[],
            'runs':{str(i):{} for i in range(4)},'limits':{'max_requests':30,'max_runs':30,'max_tokens':100000}}
        report=preflight_models(cases=4,data=ledger)
        self.assertFalse(report['ready']);self.assertEqual(report['minimum_model_calls'],48)
        self.assertEqual(report['remaining_requests'],26)

    def test_twelve_role_graph_executes_with_explicit_synthetic_sources(self):
        from integration.us_research import build_full_graph
        from langchain_core.runnables import Runnable
        from langchain_core.messages import AIMessage
        from unittest.mock import patch
        import socket
        class Offline(Runnable):
            calls=0
            def bind_tools(self,*a,**kw):return self
            def with_structured_output(self,*a,**kw):raise NotImplementedError('synthetic contract only')
            def invoke(self,*a,**kw):
                self.calls+=1
                return AIMessage(content='Synthetic research: insufficient evidence, retain uncertainty. Rating: REVIEW')
        with tempfile.TemporaryDirectory() as td:
            model=Offline();graph=build_full_graph(Path(td),model=model,memory_snapshot=[])
            state=graph.propagator.create_initial_state('NVDA','2026-10-27',instrument_context='Synthetic NVIDIA fixture',past_context='')
            with patch.object(socket.socket,'connect',side_effect=AssertionError('no real network')),\
                 patch('tradingagents.agents.analysts.sentiment_analyst.get_news') as news,\
                 patch('tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages',return_value='synthetic unavailable'),\
                 patch('tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts',return_value='synthetic unavailable'):
                news.func.return_value='synthetic unavailable'
                result=graph.graph.invoke(state,config={'recursion_limit':100})
            self.assertEqual(model.calls,12)
            self.assertIn('insufficient',result['final_trade_decision'])

if __name__=='__main__':unittest.main()
