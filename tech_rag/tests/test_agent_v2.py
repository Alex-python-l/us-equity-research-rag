import sys,unittest,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class AgentV2Tests(unittest.TestCase):
 def test_technical_coverage_recomputed_after_second_page(self):
  from research import ResearchEngine
  from agent import run_agent
  from common import ROOT,readl
  rows=[c for c in readl(ROOT/'output/chunks.jsonl') if c['source_id']=='MU_HBM4_2026' and c['paragraph'] in [11,12]]
  engine=ResearchEngine(chunks=rows,index_path=':memory:')
  result=run_agent('MU','HBM4 36GB 48GB','current','test-technical-coverage-union',max_rounds=2,engine=engine,
   budget={'use_dense':False,'max_results':1,'context_radius':0,'max_total_chars':8000,'max_tool_calls':8})
  p=result['evidence_packet'];self.assertFalse(p['question_coverage']['unmatched_technical_tokens'])
  self.assertFalse(any('尚未覆盖指定型号' in s for s in p['insufficiency_reasons']))
 def test_cross_page_evidence_union_rebuilds_assertions(self):
  from research import ResearchEngine
  from agent import run_agent
  from common import ROOT,readl
  ids={'TC_1f49272eac88ed89dff686'}
  rows=[c for c in readl(ROOT/'output/chunks.jsonl') if c['source_id']=='MU_2026_10K' and c['pdf_page']==9 and c['char_start'] in [2056,3201]]
  self.assertEqual(len(rows),2)
  engine=ResearchEngine(chunks=rows,index_path=':memory:')
  a=run_agent('MU','memory DRAM','current','test-union-v2',max_rounds=2,engine=engine,
    budget={'use_dense':False,'max_results':1,'context_radius':0,'max_total_chars':8000,'max_tool_calls':8})
  p=a['evidence_packet'];eid='EV_1f49272eac88ed89dff686';rid='TR_a19336255e7f6ed5b55b28'
  self.assertTrue(p['evidence_map'][eid]['fully_covered']);self.assertEqual(len(p['evidence_map'][eid]['citation_ids']),2)
  self.assertIn(rid,p['assertion_map']);self.assertTrue(p['paths'])
  self.assertFalse(p['coverage']['truncation']['result_page'])
 def test_followup_reuses_verified_context_in_actual_tool_input(self):
  from research import ResearchEngine
  from agent import run_agent
  engine=ResearchEngine();sid='test-followup-v2'
  b={'use_dense':False,'max_results':6}
  run_agent('MU','SOCAMM2 单CPU容量和带宽','current',sid,max_rounds=1,budget=b,engine=engine)
  a=run_agent('MU','该产品针对哪些系统？','current',sid,max_rounds=1,budget=b,engine=engine)
  self.assertTrue(any(t['tool']=='reuse_verified_context' for t in a['trace']))
  self.assertIn('SOCAMM2',a['evidence_packet']['retrieval_query'])
  self.assertTrue(any('session' in h['channel_ranks'] for h in a['evidence_packet']['results']))
 def test_company_alias_does_not_silently_select_security(self):
  from agent import run_agent
  p=run_agent('美光','存储业务','current','test-alias-v2',max_rounds=1,budget={'use_dense':False})
  self.assertIsNone(p['evidence_packet']['selected_security'])
 def test_unified_tool_called_pagination_reuse_and_history_reset(self):
  from agent import run_agent
  from research import ResearchEngine
  engine=ResearchEngine();sid='test-v2-'+uuid.uuid4().hex
  b={'use_dense':False,'max_results':2,'max_total_chars':6000,'max_tool_calls':8}
  a=run_agent('MU','HBM4 36GB 48GB 出货 送样','current',sid,max_rounds=2,budget=b,engine=engine,refresh_news=False)
  self.assertTrue(any(t['tool']=='build_research_context' for t in a['trace']))
  self.assertFalse(a['llm_generation_executed']);self.assertLessEqual(a['rounds_used'],2)
  repeat=run_agent('MU','HBM4 36GB 48GB 出货 送样','current',sid,max_rounds=2,budget=b,engine=engine,refresh_news=False)
  self.assertGreater(repeat['reused_valid_evidence_count'],0)
  h=run_agent('MU','存储业务','2025-11-01T00:00:00+00:00',sid,max_rounds=2,budget=b,engine=engine,refresh_news=False)
  import json
  text=json.dumps(h,ensure_ascii=False)
  self.assertNotIn('MU_HBM4_2026',text);self.assertNotIn('MU_2026_10K',text)
  self.assertLessEqual(h['evidence_packet']['budget_usage']['text_chars'],6000)

if __name__=='__main__':unittest.main()
