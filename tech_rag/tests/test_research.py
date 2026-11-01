import importlib.util,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ResearchTests(unittest.TestCase):
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('research'),'unified research API missing')
  return __import__('research')
 def test_chinese_uncarded_detail_is_traceable_and_not_reviewed_fact(self):
  m=self.module();p=m.build_research_context('美光','9650 SSD 28 GB/s sequential read','current',{'use_dense':False})
  json.dumps(p);self.assertEqual(p['company']['entity_id'],'MU')
  hits=[c for c in p['citations'].values() if '9650' in c['text'] and '28' in c['text']]
  self.assertTrue(hits);self.assertTrue(all(c['round_trip_verified'] for c in hits))
  self.assertTrue(any(c['review_status']=='raw_original_unreviewed_as_relation' for c in hits))
 def test_history_filters_context_and_paths(self):
  p=self.module().build_research_context('NVDA','memory suppliers TSMC Micron','2025-03-01T00:00:00+00:00',{'use_dense':False})
  from common import parse_time
  for c in p['citations'].values():
   self.assertLessEqual(parse_time(c['available_at']),parse_time(p['analysis_time']));self.assertTrue(c['historical_eligible'])
   self.assertNotEqual(c['source_id'],'NVDA_2026_10K')
  for path in p['paths']:
   for s in path['steps']:
    self.assertTrue(s['citation_ids']);self.assertTrue(set(s['citation_ids'])<=set(p['citations']))
    self.assertIn(s['traversal_direction'],['with_assertion','against_assertion'])
 def test_small_budget_continuation_no_duplicates_and_frozen_time(self):
  m=self.module();engine=m.ResearchEngine();b={'use_dense':False,'max_results':1,'max_total_chars':1800}
  a=engine.build('NVDA','suppliers memory foundries','current',b)
  self.assertTrue(a['coverage']['truncated']);self.assertTrue(a['pagination']['next_cursor'])
  c=engine.build('NVDA','suppliers memory foundries','current',b,cursor=a['pagination']['next_cursor'])
  self.assertEqual(a['analysis_time'],c['analysis_time']);self.assertFalse(set(a['citations'])&set(c['citations']))
  self.assertLessEqual(a['budget_usage']['text_chars'],1800)
 def test_missing_information_and_safe_adapter(self):
  p=self.module().build_research_context('MU','未来30天股价确定百分比是多少？','current',{'use_dense':False})
  self.assertEqual(p['status'],'insufficient');self.assertTrue(p['insufficiency_reasons'])
  self.assertIsNotNone(importlib.util.find_spec('tradingagents_adapter'),'adapter missing')
  from tradingagents_adapter import adapt_evidence_packet
  a=adapt_evidence_packet(p);self.assertEqual(a['permissions'],['research_read_only'])
  self.assertEqual(a['evidence_map'],p['citations']);self.assertIn('insufficient',a['research_context'])
 def test_timezone_and_budget_validation(self):
  m=self.module()
  with self.assertRaises(ValueError):m.build_research_context('MU','business','2025-01-01',{'use_dense':False})
  with self.assertRaises(ValueError):m.ResearchBudget.from_value({'max_hops':99})
  with self.assertRaises(ValueError):m.ResearchBudget.from_value(m.ResearchBudget(max_hops=500))
 def test_adapter_renders_context_refs_and_respects_own_text_cap(self):
  p=self.module().build_research_context('NVDA','CoWoS capacity','current',{'use_dense':False})
  from tradingagents_adapter import adapt_evidence_packet
  a=adapt_evidence_packet(p,max_context_chars=8000)
  self.assertLessEqual(len(a['research_context']),8000)
  import re
  refs=set(re.findall(r'C_[a-f0-9]{24}',a['research_context']))
  self.assertTrue(refs<=set(a['rendered_citation_ids']))
  self.assertTrue(set(a['rendered_citation_ids'])<=set(a['evidence_map']))
 def test_future_document_append_preserves_complete_historical_rank_and_context(self):
  m=self.module()
  from common import ROOT,readl
  from tempfile import TemporaryDirectory
  rows=readl(ROOT/'output/chunks.jsonl');future=dict(rows[0])
  future.update(chunk_id='FUTURE_TEST_ONLY',available_at='2028-01-01T00:00:00+00:00',text='Micron memory suppliers '*100)
  with TemporaryDirectory(dir=ROOT/'state/test_tmp') as tmp:
   a=m.ResearchEngine(chunks=rows,index_path=Path(tmp)/'old.db')
   b=m.ResearchEngine(chunks=rows+[future],index_path=Path(tmp)/'future.db')
   one=a.build('MU','memory business suppliers','2025-11-01T00:00:00+00:00',{'use_dense':False})
   two=b.build('MU','memory business suppliers','2025-11-01T00:00:00+00:00',{'use_dense':False})
   self.assertEqual(one,two)
 def test_future_assertions_cannot_select_historical_expansion_seeds(self):
  m=self.module()
  from tools import CompanyTools
  full=CompanyTools();old=CompanyTools()
  old.relations=[r for r in old.relations if r['evidence_class']=='original_disclosure']
  one=m.ResearchEngine(tools=full,index_path=':memory:').build('TSM','CoWoS','2026-10-16T23:59:59+00:00',{'use_dense':False})
  two=m.ResearchEngine(tools=old,index_path=':memory:').build('TSM','CoWoS','2026-10-16T23:59:59+00:00',{'use_dense':False})
  self.assertEqual(one,two)

if __name__=='__main__':unittest.main()
