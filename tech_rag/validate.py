"""Acceptance checks on real artifacts, directed paths, historical leakage and agent state."""
from common import *
from collections import Counter
import hashlib,re,base64
import numpy as np
from tools import CompanyTools
from agent import run_agent

def run(backend='local',config=None,refresh_news=True):
 T=CompanyTools(backend,config);checks=[]
 def artifact(name):return ROOT/'output'/(name if backend=='local' else name.replace('.json','_neo4j.json'))
 def check(name,condition,details=None):
  checks.append({'name':name,'passed':bool(condition),'details':details})
  if not condition:raise AssertionError(name)
 matrix=np.load(ROOT/'output/embeddings.npy',allow_pickle=False);records=readl(ROOT/'output/embedded_records.jsonl')
 check('embedding_shape_and_norm',matrix.shape==(len(records),1024) and np.isfinite(matrix).all() and np.allclose(np.linalg.norm(matrix,axis=1),1,atol=1e-5),list(matrix.shape))
 check('no_unresolved_curated_claims',not load(ROOT/'output/curation_gaps.json'))
 check('relations_have_known_endpoints',all(r['subject_id'] in T.entities and r['object_id'] in T.entities for r in T.relations))
 check('relations_have_scope_conditions_type_and_evidence',all(r['business_scope'] and r['conditions'] and r['predicate'] and r['evidence_ids'] for r in T.relations))
 aliases={'MU':'Micron','SKH':'SK Hynix','SAMSUNG':'Samsung','TSM':'TSMC','FOXCONN':'Hon Hai','WISTRON':'Wistron','FABRINET':'Fabrinet'}
 trades=[r for r in T.relations if r['directness'].startswith('direct_named')]
 check('named_trade_parties_present_in_evidence',all(aliases[r['subject_id']].lower() in ' '.join(T.evidence[e]['quote'] for e in r['evidence_ids']).lower() for r in trades),len(trades))
 check('research_and_model_edges_not_marked_as_facts',all(not r['fact_edge_eligible'] for r in T.relations if r['evidence_class']!='original_disclosure'))
 chunks=readl(ROOT/'output/chunks.jsonl');pages={};bad_chunks=[]
 for sid in {c['source_id'] for c in chunks}:
  pages[sid]={(b.get('pdf_page'),b.get('paragraph')):re.sub(r'\s+',' ',b['text']).strip() for b in readl(ROOT/T.sources[sid]['text_path'])}
 for c in chunks:
  text=pages[c['source_id']].get((c['pdf_page'],c['paragraph']),'')
  if text[c['char_start']:c['char_end']]!=c['text'] or c['source_sha256']!=T.sources[c['source_id']]['sha256']:bad_chunks.append(c['chunk_id'])
 check('all_text_chunks_round_trip',not bad_chunks,{'checked':len(chunks),'failed_first_ids':bad_chunks[:3]})
 checked=T.evidence_lookup(list(T.evidence),'current');check('all_reviewed_evidence_round_trips',len(checked['items'])==len(T.evidence) and not checked['missing'],len(checked['items']))
 source_ids={e['source_id'] for e in T.evidence.values()}
 for sid in source_ids:
  source=T.sources[sid]
  with (ROOT/source['local_path']).open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  check('source_hash_'+sid,h==source['sha256'])
 profiles=[]
 for ticker in ['MU','TSM','NVDA']:
  result=T.company_relations(ticker,'current',page_size=10)
  profiles.append(result);dump(artifact(f'company_{ticker}.json'),result)
  check(ticker+'_entry_nonempty',bool(result['main_business']) and result['pagination']['enumerated_paths']>0)
  for path in result['paths']:
   check('path_continuity_'+path['path_id'],[s['order'] for s in path['steps']]==list(range(1,len(path['steps'])+1)) and all(path['steps'][i]['to_id']==path['steps'][i+1]['from_id'] for i in range(len(path['steps'])-1)))
  cursor=result['pagination']['next_cursor']
  if cursor:
   page2=T.company_relations(ticker,'current',page_size=10,cursor=cursor)
   check(ticker+'_pagination_no_duplicates',not ({p['path_id'] for p in result['paths']}&{p['path_id'] for p in page2['paths']}))
   check(ticker+'_pagination_time_frozen',result['analysis_time']==page2['analysis_time'])
 n=next(p for p in profiles if p['ticker']=='NVDA');mu=next(p for p in profiles if p['ticker']=='MU')
 check('NVDA_direct_named_suppliers_include_MU_TSM',{'MU','TSM'}<=set(r['subject_id'] for r in n['direct_upstream']))
 check('MU_TSM_technical_alliance_not_supply',not any(r['subject_id']=='TSM' for r in mu['direct_upstream']) and any(r['predicate']=='COLLABORATES_WITH' and r['object_id']=='TSM' for r in T.relations))
 anonymous=[e for e in T.entities.values() if e['kind']=='AnonymousCustomer']
 check('anonymous_stays_anonymous',all(not e.get('ticker') and ('anonymous' in e['name'].lower()) for e in anonymous))
 truncated=T.company_relations('NVDA','current',page_size=5,max_paths=10)
 check('traversal_cap_disclosed',truncated['coverage']['enumeration_truncated'] and not truncated['coverage']['total_paths_known_exactly'])
 before=T.company_relations('MU','2026-10-08T23:59:59+00:00',page_size=100)
 old_sources={e['source_id'] for p in before['paths'] for step in p['steps'] for e in step['evidence']}
 check('historical_rejects_later_MU_2026_filing','MU_2026_10K' not in old_sources)
 check('historical_keeps_available_prior_MU_filing','MU_2025_10K' in old_sources)
 future_entity=T.expand_entity('MU_CMBU','2025-11-01T00:00:00+00:00')
 check('historical_expansion_hides_later_entity_metadata',not future_entity['found'] and 'entity' not in future_entity)
 for p in before['paths']:
  check('historical_relation_not_future_'+p['path_id'],all(parse_time(s['available_at'])<=parse_time(before['analysis_time']) for s in p['steps']))
 historical_scenario=T.scenario('NVDA','hbm_shortage','2025-06-01T00:00:00+00:00')
 check('historical_does_not_backfill_current_inference',historical_scenario['insufficient'] and not historical_scenario['impact_paths'])
 scenarios={}
 for ticker in ['MU','TSM','NVDA']:
  for kind in ['hbm_shortage','packaging_capacity']:
   scenario=T.scenario(ticker,kind,'current');scenarios[ticker+'_'+kind]=scenario
   check(ticker+'_'+kind+'_assumptions_disclosed',bool(scenario['unknowns']) and all(step.get('evidence_class') and step.get('evidence_ids') for p in scenario['impact_paths'] for step in p['steps']))
 dump(artifact('scenario_validation.json'),scenarios)
 session='validation_reuse' if backend=='local' else 'validation_neo4j_reuse'
 a1=run_agent('MU','近期HBM供应受限及产品进展','current',session,backend,config,2,8,refresh_news=refresh_news)
 a2=run_agent('MU','近期HBM供应受限及产品进展','current',session,backend,config,2,8,refresh_news=refresh_news)
 check('agent_calls_independent_news',any(t['tool']=='independent_news_search' for t in a1['trace']),a1['result']['news']['live_refresh_status'])
 check('agent_bounded_rounds',a1['rounds_used']<=2 and a2['rounds_used']<=2)
 discovered={r[k] for e in a1['result']['expanded_entities'] for r in e.get('relations',[]) for k in ['subject_id','object_id']}
 check('agent_expansion_tracks_discovered_endpoints',discovered<=set(a1['new_entity_ids'])|set(a1['trace'][0]['known_entities_before']))
 check('agent_cross_turn_evidence_reuse',a2['reused_valid_evidence_count']>0,a2['reused_valid_evidence_count'])
 a3=run_agent('MU','存储业务有哪些','2025-11-01T00:00:00+00:00',session,backend,config,2,8)
 text=json.dumps(a3,ensure_ascii=False)
 check('agent_memory_cannot_leak_later_company_units','MU_CMBU' not in text and 'Vera Rubin' not in text and 'MU_2026_10K' not in text)
 dump(artifact('agent_validation_examples.json'),{'current_first_turn':a1,'current_second_turn':a2,'historical_recheck':a3})
 # Save machine-readable company dossiers and all checks, without overstating DB status.
 dump(artifact('company_profiles.json'),profiles)
 report={'passed':all(c['passed'] for c in checks),'backend':backend,'check_count':len(checks),'checks':checks,'checked_at':now(),'agent_version':'unified_research_v2',
  'entities':len(T.entities),'relations':len(T.relations),'evidence':len(T.evidence),'embedded_records':len(records),
  'neo4j_tests_executed':backend=='neo4j','desktop_ui_verified':False,'llm_generation_tested':False,
  'live_news_status':a1['result']['news']['live_refresh_status'],'strict_intraday_backtest_ready':False}
 dump(ROOT/'output'/('validation_neo4j.json' if backend=='neo4j' else 'validation_local.json'),report)
 print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False,indent=2))

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--backend',choices=['local','neo4j'],default='local');p.add_argument('--config');p.add_argument('--no-live-news',action='store_true')
 a=p.parse_args();run(a.backend,a.config,refresh_news=not a.no_live_news)
