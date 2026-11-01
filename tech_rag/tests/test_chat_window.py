"""RAG-only chat contracts, synthetic fixture text is not real disclosure."""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def packet(question='业务'):
    return {'question':question,'analysis_time':'2026-10-20T00:00:00+00:00','temporal_mode':'current_as_collected',
        'status':'partial','citations':{'C1':{'citation_id':'C1','source_id':'SRC','source_sha256':'a'*64,
        'title':'Original disclosure','text':'Company makes memory. Technical compatibility is not a purchase contract.',
        'source_url':'https://issuer.example/report','available_at':'2026-10-01T00:00:00+00:00',
        'paragraph':3,'pdf_page':None,'char_start':0,'char_end':84,'round_trip_verified':True,
        'overlapping_reviewed_evidence_ids':[],'excerpt_truncated':False}},
        'assertion_map':{},'paths':[],'unknowns':['Unknown customer allocation.'],
        'insufficiency_reasons':[],'coverage':{'truncated':True,'eligible_original_chunks':100},
        'pagination':{'next_cursor':None},'fingerprints':{'eligible_data':'abc','embedding_model':'BGE-M3'}}


class ChatWindowTests(unittest.TestCase):
    def service(self):
        from chat_service import RAGChat
        return RAGChat

    def test_evidence_mode_never_initializes_provider(self):
        calls=[]
        def retrieve(*args,**kwargs):calls.append(kwargs);return packet()
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=retrieve,completion=lambda *a,**k:self.fail('provider called'),state_dir=td)
            out=chat.ask({'ticker':'美光','question':'主营业务','mode':'evidence','use_dense':False})
        self.assertEqual(out['company'],'MU');self.assertEqual(out['usage']['model_requests'],0)
        self.assertEqual(out['sources'][0]['citation_id'],'C1');self.assertEqual(out['sources'][0]['review_status'],'raw_excerpt_not_assertion_fact')

    def test_no_original_citations_means_no_model_call(self):
        p=packet();p['citations']={}
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:p,completion=lambda *a,**k:self.fail('provider called'),state_dir=td)
            out=chat.ask({'ticker':'MU','question':'谁是匿名客户','mode':'answer'})
        self.assertEqual(out['status'],'insufficient');self.assertEqual(out['usage']['model_requests'],0)

    def test_answer_receives_actual_quotes_and_no_news_or_old_model_claims(self):
        got=[]
        def answer(context):got.append(context);return {'answer':'公司生产内存。[1]','usage':{'total_tokens':20}}
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:packet(),completion=answer,state_dir=td)
            out=chat.ask({'ticker':'MU','question':'业务','mode':'answer','history':[{'role':'assistant','content':'invented supplier'}]})
        self.assertIn('Company makes memory.',str(got[0]));self.assertNotIn('invented supplier',str(got[0]))
        self.assertEqual(out['answer'],'公司生产内存。[1]');self.assertEqual(out['usage']['total_tokens'],20)

    def test_external_news_questions_retrieve_chain_instead_of_disclosure_channels(self):
        from research import question_route
        queries=[];contexts=[]
        def retrieve(ticker,q,as_of,**kwargs):queries.append(q);return packet(q)
        def answer(context):
            contexts.append(context)
            return {'answer':'- 上游内存：查供应、产能。[1]','usage':{'total_tokens':20}}
        questions=[
            '我现在想要了解英伟达的股票，除了本公司的新闻，还应该获取哪些信息',
            '我想研究英伟达的股票，应该查哪些新闻，除了本公司的，是外界对于本公司的',
            '补充英伟达上下游的相关产业链，给新闻agent搜索方向',
        ]
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=retrieve,completion=answer,state_dir=td)
            for q in questions:
                out=chat.ask({'ticker':'NVDA','question':q,'mode':'answer'})
                self.assertEqual(out.get('answer_intent'),'news_search_brief')
                self.assertEqual(out['question'],q)
                self.assertEqual(question_route(queries[-1])['intent'],'company_relationships')
                self.assertIn('供应商',queries[-1]);self.assertIn('客户',queries[-1])
                self.assertEqual(contexts[-1]['question'],q)
                self.assertEqual(contexts[-1]['answer_intent'],'news_search_brief')
                self.assertFalse(out['live_news_used'])

    def test_elliptical_news_followup_keeps_search_brief_intent(self):
        contexts=[]
        def answer(context):
            contexts.append(context)
            return {'answer':'- 下游：查需求。[1]','usage':{'total_tokens':10}}
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:packet(),completion=answer,state_dir=td)
            out=chat.ask({'ticker':'NVDA','question':'那下游呢','history':[
                {'role':'user','content':'除了英伟达本公司的新闻，还应该查什么','ticker':'NVDA','as_of':'current'}]})
        self.assertEqual(out.get('answer_intent'),'news_search_brief')
        self.assertIn('那下游呢',out['retrieval_question'])
        self.assertEqual(contexts[0]['question'],'那下游呢')

    def test_specific_fact_questions_do_not_become_search_plans(self):
        queries=[]
        def retrieve(ticker,q,as_of,**kwargs):queries.append(q);return packet(q)
        question='美光和台积电的HBM合作，是供货关系还是工艺兼容合作？'
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=retrieve,completion=lambda c:{'answer':'兼容不等于采购。[1]','usage':{'total_tokens':10}},state_dir=td)
            out=chat.ask({'ticker':'MU','question':question})
        self.assertEqual(queries,[question])
        self.assertEqual(out.get('answer_intent'),'grounded_answer')

    def test_followup_never_carries_current_evidence_into_history(self):
        contexts=[]
        def retrieve(ticker,q,as_of,**kwargs):contexts.append(q);return packet(q)
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=retrieve,state_dir=td)
            chat.ask({'ticker':'MU','question':'那有客户名字吗','mode':'evidence','as_of':'2024-01-01T00:00:00+00:00',
                'history':[{'role':'user','content':'HBM4供应份额','ticker':'MU','as_of':'current'}]})
        self.assertNotIn('HBM4供应份额',contexts[0])

    def test_followup_question_scope_reaches_answer_model_without_old_answers(self):
        got=[]
        def answer(context):
            got.append(context)
            return {'answer':'兼容不等于采购。[1]','usage':{'total_tokens':10}}
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:packet(),completion=answer,state_dir=td)
            chat.ask({'ticker':'MU','question':'那能证明采购吗','mode':'answer','history':[
                {'role':'user','content':'美光与台积电HBM兼容合作','ticker':'MU','as_of':'current'},
                {'role':'assistant','content':'invented supplier','ticker':'MU','as_of':'current'}]})
        self.assertIn('美光与台积电HBM兼容合作',got[0]['retrieval_question'])
        self.assertEqual(got[0]['question'],'那能证明采购吗')
        self.assertNotIn('invented supplier',str(got[0]))

    def test_invalid_reference_is_flagged_not_presented_as_verified(self):
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:packet(),completion=lambda c:{'answer':'Company supplies everyone.[99]','usage':{'total_tokens':10}},state_dir=td)
            out=chat.ask({'ticker':'MU','question':'业务','mode':'answer'})
        self.assertEqual(out['status'],'needs_review');self.assertTrue(out['warnings'])

    def test_history_requires_timezone_and_unknown_company_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=lambda *a,**k:self.fail('retrieval before validation'),state_dir=td)
            for body in ({'ticker':'AAPL','question':'业务'}, {'ticker':'MU','question':'业务','as_of':'2024-01-01'}):
                with self.assertRaises(ValueError):chat.ask(body)

    def test_pagination_keeps_frozen_clock_and_does_not_regenerate_answer(self):
        calls=[];p=packet();p['pagination']={'next_cursor':'next'}
        def retrieve(ticker,q,as_of,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:return p
            nxt=packet();nxt['citations']={'C2':{**nxt['citations']['C1'],'citation_id':'C2'}};return nxt
        with tempfile.TemporaryDirectory() as td:
            chat=self.service()(retrieve=retrieve,completion=lambda c:self.fail('model not needed'),state_dir=td)
            first=chat.ask({'ticker':'MU','question':'业务','mode':'evidence'})
            more=chat.more(first['query_id'])
        self.assertEqual(calls[1]['temporal_context'],{'analysis_time':p['analysis_time'],'historical':False})
        self.assertEqual(more['sources'][0]['number'],2);self.assertFalse(more['sources'][0]['used_in_answer'])


class ChatHTTPTests(unittest.TestCase):
    def test_local_UI_and_CSRF_gate_precede_any_chat_call(self):
        from chat_server import make_handler
        from http.server import ThreadingHTTPServer
        import threading,http.client
        class Fake:
            def ask(self,body):raise AssertionError('untrusted request reached chat')
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(Fake(),'test-token'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET','/');r=client.getresponse();html=r.read().decode()
            self.assertEqual(r.status,200);self.assertIn('test-token',html);self.assertIn('和资料对话',html)
            client.request('POST','/api/chat','{}',headers={'Content-Type':'application/json'})
            r=client.getresponse();r.read();self.assertEqual(r.status,403)
            client.request('POST','/api/chat','{}',headers={'Content-Type':'application/json','X-CSRF-Token':'test-token','Origin':'https://foreign.example'})
            r=client.getresponse();r.read();self.assertEqual(r.status,403)
            client.request('GET','/',headers={'Host':'foreign.example'})
            r=client.getresponse();r.read();self.assertEqual(r.status,403);client.close()
        finally:server.shutdown();server.server_close()


if __name__=='__main__':unittest.main()
