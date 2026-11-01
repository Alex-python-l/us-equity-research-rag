"""Current news candidates are available; historical/evidence gates stay closed."""
import importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

ARTICLES=[
 {'content':{'title':'Micron announces product update','summary':'Unverified provider summary',
  'pubDate':'2026-10-17T12:00:00Z','provider':{'displayName':'Example publisher'},
  'canonicalUrl':{'url':'https://example.com/micron-update'}}},
 {'content':{'title':'Future update','pubDate':'2026-10-19T12:00:00Z','canonicalUrl':{'url':'https://example.com/future'}}},
 {'content':{'title':'Unknown time','canonicalUrl':{'url':'https://example.com/unknown'}}},
 {'content':{'title':'Old update','pubDate':'2020-01-01T00:00:00Z','canonicalUrl':{'url':'https://example.com/old'}}},
]

class LiveNewsTests(unittest.TestCase):
 def module(self):
  self.assertIsNotNone(importlib.util.find_spec('integration.live_news'),'current news SDK adapter missing')
  from integration import live_news
  return live_news
 def test_current_news_filters_future_missing_time_and_preserves_candidate_status(self):
  m=self.module()
  with patch.object(m,'_fetch_yahoo',return_value=ARTICLES+[ARTICLES[0]]):
   r=m.search_live_news('美光','2026-10-18T12:00:00+00:00',False)
  self.assertEqual(len(r['items']),1);c=r['items'][0]
  self.assertEqual(c['title'],'Micron announces product update')
  self.assertFalse(c['body_acquired']);self.assertFalse(c['source_verified']);self.assertFalse(c['historical_eligible'])
  self.assertEqual(c['status'],'candidate_metadata')
 def test_historical_query_never_contacts_live_provider(self):
  m=self.module()
  def forbidden(*a,**k):raise AssertionError('historical mode contacted live news')
  with patch.object(m,'_fetch_yahoo',side_effect=forbidden):r=m.search_live_news('MU','2025-01-01T00:00:00+00:00',True)
  self.assertEqual(r['status'],'unsupported_historical');self.assertEqual(r['items'],[])
 def test_provider_failure_is_unavailable_not_absence_of_news(self):
  m=self.module()
  with patch.object(m,'_fetch_yahoo',side_effect=TimeoutError('private provider detail')):
   r=m.search_live_news('MU','2026-10-18T12:00:00+00:00',False)
  self.assertEqual(r['status'],'provider_unavailable');self.assertNotIn('private provider detail',json.dumps(r))
 def test_online_tool_contract_discovery_then_rag_and_history_excludes_discovery(self):
  from datetime import datetime,timezone
  from types import SimpleNamespace
  from integration.runtime import EvidenceRuntime
  from integration.models import RoleModel
  cfg=SimpleNamespace(model='test-model',base_url='https://example.invalid',api_key='unit-test-not-a-secret')
  rt=EvidenceRuntime('MU','HBM',datetime.now(timezone.utc).isoformat(),False,use_dense=False,mode='online',llm_config=cfg)
  rt.build_tools();rt.source_ids.add('MU_HBM4_2026');model=RoleModel(rt,'news',rt.tools)
  payloads=[]
  def response(url,headers,json,timeout):
   payloads.append(json);return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
  with patch('requests.post',side_effect=response):
   for _ in range(3):model._online([], {})
  self.assertEqual([p['tool_choice']['function']['name'] for p in payloads],['search_news','company_research','source_followup'])
  old=EvidenceRuntime('MU','HBM','2025-01-01T00:00:00+00:00',True,use_dense=False,mode='online',llm_config=cfg)
  old.build_tools();self.assertNotIn('search_news',[t.name for t in old.tools])
 def test_current_permission_enabled_without_enabling_market_or_historical_news(self):
  from integration.runtime import tool_permissions
  self.assertTrue(tool_permissions(False)['live_news']['enabled'])
  self.assertFalse(tool_permissions(True)['live_news']['enabled'])
  self.assertFalse(tool_permissions(False)['live_market']['enabled'])
 @unittest.skipUnless(importlib.util.find_spec('langgraph'),'integration interpreter required')
 def test_real_graph_news_search_is_not_promoted_to_evidence(self):
  m=self.module()
  from datetime import datetime
  from integration.framework import run_archived_research_v3 as run_research
  # Fixed fixture dates need a fixed current-mode clock; otherwise all test
  # articles expire after seven real calendar days and the assertion drifts.
  with patch.object(m,'_fetch_yahoo',return_value=ARTICLES),patch('integration.framework.freeze',return_value=(datetime.fromisoformat('2026-10-18T12:00:00+00:00'),False)):
   r=run_research('MU','最新HBM相关新闻及供应链影响','current',use_dense=False,fetch_news=True)
  names=[t['tool'] for t in r['tool_trace']]
  self.assertIn('search_news',names);self.assertIn('company_research',names)
  self.assertLess(names.index('search_news'),names.index('company_research'))
  self.assertTrue(r['news_candidates'])
  for c in r['news_candidates']:
   self.assertNotIn(c['candidate_id'],r['evidence_registry'])
  self.assertEqual(r['research_gate']['signal'],'no_signal')
  self.assertLessEqual(r['run_usage']['llm_calls'],12)

if __name__=='__main__':unittest.main()
