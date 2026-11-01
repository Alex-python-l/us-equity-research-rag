import sys, unittest, importlib.util, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import ResearchEngine
from aliases import expand_query
from lexical import tokenize
from common import ROOT, load
from evaluate_research import anchor_hit, packet_checks


class RetrievalV3Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.engine=ResearchEngine(index_path=ROOT/'state/test_v3_lexical.sqlite3')
  cls.questions={q['id']:q for q in load(ROOT/'evaluation/questions_v2.json')}

 def packet(self,qid):
  q=self.questions[qid]
  p=self.engine.build(q['ticker'],q['question'],q['as_of'],{**q.get('budget_override',{}),'use_dense':False})
  return q,p

 def assert_anchors(self,q,p):
  misses=[a['needle'] for a in q['expected_evidence'] if not anchor_hit(self.engine,a,p['citations'])['full_hit']]
  self.assertEqual(misses,[])
  checked=packet_checks(self.engine,p)
  self.assertEqual(checked['citation_errors'],[]);self.assertEqual(checked['historical_leaks'],[])
  self.assertLessEqual(p['budget_usage']['text_chars'],p['budget']['max_total_chars'])
  self.assertLessEqual(p['budget_usage']['retrieval_tool_calls'],p['budget']['max_tool_calls'])

 def test_chinese_memory_and_operating_segment_have_english_matches(self):
  self.assertIn('memory',expand_query('内存供应伙伴'))
  self.assertIn('operating',expand_query('经营分部'))
  self.assertIn('purchase',expand_query('采购'))

 def test_english_inflections_share_search_tokens(self):
  self.assertTrue(set(tokenize('foundries')) & set(tokenize('foundry')))
  self.assertTrue(set(tokenize('suppliers')) & set(tokenize('supplier')))
  self.assertTrue(set(tokenize('collaborating')) & set(tokenize('collaboration')))

 def test_historical_supplier_paragraph_is_returned_without_future_version(self):
  q,p=self.packet('V2_18');self.assert_anchors(q,p)
  q,p=self.packet('V2_22');self.assert_anchors(q,p)
  self.assertNotIn('NVDA_2026_10K',{c['source_id'] for c in p['citations'].values()})

 def test_cuda_and_both_operating_segments_are_returned(self):
  q,p=self.packet('V2_02');self.assert_anchors(q,p)

 def test_collaboration_keeps_cross_company_source_and_anonymous_disclosure(self):
  q,p=self.packet('V2_06');self.assert_anchors(q,p)
  self.assertIn('MU_2026_10K',{c['source_id'] for c in p['citations'].values()})
  q,p=self.packet('V2_08');self.assert_anchors(q,p)
  self.assertEqual(p['status'],'insufficient')

 def test_cowos_variants_and_numeric_source_blocks_share_original_budget(self):
  # A single packet only has two result slots in this frozen question. The
  # multi-page agent must retrieve the rest within the unchanged 8000 chars.
  from agent import run_agent
  q=self.questions['V2_24']
  result=run_agent(q['ticker'],q['question'],q['as_of'],'test-v3-cowos-'+uuid.uuid4().hex,
   max_rounds=5,budget={**q['budget_override'],'use_dense':False},engine=self.engine,refresh_news=False)
  self.assert_anchors(q,result['evidence_packet'])
  self.assertLessEqual(result['evidence_packet']['budget_usage']['retrieval_tool_calls'],8)
  self.assertEqual(result['reused_valid_evidence_count'],0)
  if result['evidence_packet']['pagination']['page_limit_reached']:
   self.assertEqual(result['stop_reason'],'page_limit')

 def test_facet_candidates_do_not_filter_cross_company_disclosures(self):
  from lexical import KeywordIndex,freeze
  from retrieval_v3 import keyword_candidates
  row={'chunk_id':'cross','ticker':'NVDA','source_id':'other_company','text':'We purchase memory from Micron.',
   'available_at':'2024-01-01T00:00:00+00:00','historical_eligible':True}
  idx=KeywordIndex(':memory:',[row]);at,h=freeze('2025-01-01T00:00:00+00:00')
  result=keyword_candidates(idx,'内存供应商','MU',at,h,6,3)
  self.assertEqual([x['chunk_id'] for x in result['hits']],['cross'])
  self.assertLessEqual(sum(q['returned'] for q in result['queries']),6)
  self.assertLessEqual(result['query_calls'],3)

 def test_baseline_comparison_executes_isolated_old_code_without_changing_budget(self):
  self.assertIsNotNone(importlib.util.find_spec('compare_retrieval_v3'),'isolated old-code comparison API missing')
  from compare_retrieval_v3 import baseline_packets
  request={'ticker':'NVDA','question':'memory suppliers','as_of':'2025-03-01T00:00:00+00:00','budget':{'use_dense':False}}
  packet=baseline_packets([request])[0]
  self.assertEqual(packet['budget']['keyword_candidates'],60)
  self.assertIn('v2',packet['fingerprints']['retrieval_algorithm'])
  self.assertTrue(packet['citations'])

 def test_framework_frozen_current_clock_reuses_exact_temporal_context(self):
  import inspect
  self.assertIn('temporal_context',inspect.signature(self.engine.build).parameters)
  clock={'analysis_time':'2026-10-17T12:00:00+00:00','historical':False}
  b={'use_dense':False,'max_results':1}
  p=self.engine.build('NVDA','memory suppliers','current',b,temporal_context=clock)
  self.assertEqual(p['analysis_time'],clock['analysis_time'])
  self.assertEqual(p['temporal_mode'],'current_as_collected')
  following=self.engine.build('NVDA','memory suppliers','current',b,cursor=p['pagination']['next_cursor'],temporal_context=clock)
  self.assertEqual(following['analysis_time'],clock['analysis_time'])

 def test_invalid_temporal_context_cannot_override_historical_or_future_clock(self):
  import inspect
  self.assertIn('temporal_context',inspect.signature(self.engine.build).parameters)
  cases=[('current',{'analysis_time':'2099-01-01T00:00:00+00:00','historical':False}),
   ('current',{'analysis_time':'2025-01-01','historical':False}),
   ('current',{'analysis_time':'2025-01-01T00:00:00+00:00','historical':True}),
   ('current',{'analysis_time':'2025-01-01T00:00:00+00:00','historical':0}),
   ('2025-03-01T00:00:00+00:00',{'analysis_time':'2025-03-02T00:00:00+00:00','historical':True}),
   ('2025-03-01T00:00:00+00:00',{'analysis_time':'2025-03-01T00:00:00+00:00','historical':False})]
  for as_of,clock in cases:
   with self.subTest(as_of=as_of,clock=clock),self.assertRaises(ValueError):
    self.engine.build('NVDA','memory',as_of,{'use_dense':False},temporal_context=clock)


if __name__=='__main__':unittest.main()
