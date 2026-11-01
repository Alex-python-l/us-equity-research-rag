"""Bounded deterministic orchestration of the unified research API. No LLM planner."""
from common import *
from contextlib import closing
import sqlite3,re,copy
from research import ResearchEngine,ResearchBudget,question_route,technical_coverage
from lexical import eligible,freeze
from aliases import resolve_company
from news_tool import news_search

def route(question):
 r=question_route(question)
 return {**r,'needs_independent_news':r['needs_dynamic_news'],
  'scenario':'hbm_shortage' if re.search('HBM',question,re.I) and re.search('受限|短缺|shortage',question,re.I) else (
   'packaging_capacity' if re.search('CoWoS|封装|packaging',question,re.I) and re.search('产能|capacity',question,re.I) else None)}

def run_agent(ticker,question,as_of,session_id='default',backend='local',config=None,max_rounds=3,page_size=15,
              *,budget=None,engine=None,refresh_news=True):
 if not 1<=max_rounds<=5:raise ValueError('max_rounds must be 1..5')
 engine=engine or ResearchEngine(backend,config);symbol=resolve_company(ticker)['research_symbol']
 values={'max_results':page_size,'max_rounds':max_rounds,'max_pages':max_rounds,**(budget or {})}
 b=ResearchBudget.from_value(values);intent=route(question)
 with closing(sqlite3.connect(ROOT/'state/agent.sqlite3')) as conn,conn:
  conn.execute('CREATE TABLE IF NOT EXISTS research_sessions_v2 (session_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
  old=conn.execute('SELECT payload FROM research_sessions_v2 WHERE session_id=?',(session_id,)).fetchone()
  previous=json.loads(old[0]) if old else {'citations':{},'turns':0,'gaps':[]}
  at,historical=freeze(as_of);fp=engine.fingerprint(at,historical)
  same_mode=previous.get('temporal_mode')==('strict_historical' if historical else 'current_as_collected')
  reusable={}
  if same_mode and previous.get('data_fingerprint')==fp:
   for ref,c in previous['citations'].items():
    current=engine.chunks_by_id.get(c['chunk_id'])
    if current and eligible(current,at,historical) and current['source_sha256']==c['source_sha256'] and engine.verify_chunk(current)[0]:reusable[ref]=c
  allowed_relations=[r for r in engine.tools.relations if engine.tools.eligible(r,at,historical)[0]]
  allowed_entities={r[k] for r in allowed_relations for k in ['subject_id','object_id']}
  known=[x for x in previous.get('known_entity_ids',[]) if x in allowed_entities] if same_mode else []
  context={'company':symbol,'data_fingerprint':fp,'chunk_ids':sorted({c['chunk_id'] for c in reusable.values()}),
   'focus_terms':previous.get('focus_terms',[]),'seed_entity_ids':known} if reusable and previous.get('company')==symbol else None
  trace=[{'round':0,'tool':'inspect_existing_state','known_entities_before':known,'known_evidence_before':len(reusable),
   'existing_gap_count_checked':len(previous.get('gaps',[])),'time_and_data_version_rechecked':True,
   'cache_reset':not(same_mode and previous.get('data_fingerprint')==fp)}]
  aggregate=None;cursor=None;rounds=0;calls=0;stop='no_new_information';news=None
  for n in range(min(max_rounds,b.max_rounds,b.max_pages)):
   remaining=b.max_total_chars-(aggregate['budget_usage']['text_chars'] if aggregate else 0)
   if remaining<100:stop='total_text_budget';break
   if calls>=b.max_tool_calls and not engine.has_cached_cursor(cursor):stop='tool_budget';break
   page=engine.build(ticker,question,as_of,b,cursor=cursor,session_context=context)
   page_calls=page['budget_usage']['retrieval_tool_calls']
   calls+=page_calls;rounds+=1
   trace.append({'round':n+1,'tool':'build_research_context','route':page['route'],'returned':len(page['results']),
    'pagination':page['pagination'],'data_fingerprint':page['fingerprints']['eligible_data']})
   trace.extend({'round':n+1,**t} for t in page['trace'])
   if aggregate is None:aggregate=copy.deepcopy(page)
   else:
    newrefs=set(page['citations'])-set(aggregate['citations'])
    if not newrefs:stop='no_new_information';break
    new_assertions={k:v for k,v in page['assertion_map'].items() if k not in aggregate['assertion_map']}
    cost=sum(len(page['citations'][k]['text']) for k in newrefs)+sum(len(r['claim'])+len(r['conditions'])+len(r['business_scope']) for r in new_assertions.values())
    if cost>remaining:stop='total_text_budget';break
    aggregate['citations'].update({k:page['citations'][k] for k in newrefs})
    aggregate['results'].extend(h for h in page['results'] if h['citation_id'] in newrefs)
    aggregate['assertion_map'].update(new_assertions);aggregate['evidence_map'].update(page['evidence_map'])
    existing={p['path_id'] for p in aggregate['paths']}
    aggregate['paths'].extend(p for p in page['paths'] if p['path_id'] not in existing)
    aggregate['paths']=aggregate['paths'][:b.max_paths]
    for cat,ids in page['company_profile'].items():aggregate['company_profile'][cat]=sorted(set(aggregate['company_profile'][cat]+ids))
    aggregate['budget_usage']['text_chars']+=cost;aggregate['pagination']=page['pagination']
    aggregate['coverage']=copy.deepcopy(page['coverage'])
   cursor=page['pagination']['next_cursor']
   if not cursor:
    stop='page_limit' if page['pagination']['page_limit_reached'] else 'eligible_candidate_pages_exhausted'
    break
   # Reserve the same route's call cost before another page. All pages share
   # parameters so their query scope remains reproducible.
   if calls+page_calls>b.max_tool_calls and not engine.has_cached_cursor(cursor):stop='tool_budget';break
   stop='round_limit'
  if aggregate is None:raise RuntimeError('no initial research page within budget')
  reconciled=engine.assemble(aggregate['citations'],parse_time(aggregate['analysis_time']),historical,b,symbol,engine._last_graph_paths)
  for field in ['assertion_map','evidence_map','paths','company_profile']:aggregate[field]=reconciled[field]
  aggregate['budget_usage']['text_chars']=reconciled['text_chars']
  aggregate['question_coverage']=technical_coverage(aggregate['retrieval_query'],aggregate['citations'])
  aggregate['insufficiency_reasons']=[s for s in aggregate['insufficiency_reasons'] if '尚未覆盖指定型号' not in s and not (aggregate['citations'] and s.startswith('本页没有可定位'))]
  missing=aggregate['question_coverage']['unmatched_technical_tokens']
  if missing:aggregate['insufficiency_reasons'].append('聚合原文尚未覆盖指定型号/数值标识：'+', '.join(missing)+'；不能以相近产品推断。')
  aggregate['coverage']['truncation']['result_page']=bool(aggregate['pagination']['next_cursor']) or aggregate['pagination']['page_limit_reached']
  aggregate['coverage']['truncation']['path_limit']=reconciled['paths_omitted_by_budget']>0
  aggregate['coverage']['truncation']['assertion_text_budget']=reconciled['assertions_omitted_by_budget']>0
  aggregate['coverage']['truncated']=any(aggregate['coverage']['truncation'].values())
  aggregate['status']='insufficient' if aggregate['insufficiency_reasons'] else ('partial' if aggregate['coverage']['truncated'] else 'evidence_found')
  trace.append({'round':rounds,'tool':'reconcile_page_evidence','fully_covered_evidence':sum(e['fully_covered'] for e in reconciled['evidence_map'].values()),
   'assertions':len(reconciled['assertion_map']),'paths':len(reconciled['paths'])})
  if intent['needs_independent_news'] and calls<b.max_tool_calls:
   news=news_search(symbol,as_of,question,refresh=refresh_news)
   news['items']=[item for item in news['items'] if eligible(item,parse_time(aggregate['analysis_time']),historical)]
   trace.append({'round':rounds,'tool':'independent_news_search','live_refresh_status':news['live_refresh_status'],
    'candidate_count':len(news['items']),'metadata_is_evidence':False});calls+=1
  refs=set(aggregate['citations']);reused=len(refs&set(reusable))
  entities={symbol}|{x for p in aggregate['paths'] for x in p['nodes']}
  aggregate['budget_usage']['retrieval_tool_calls']=calls
  aggregate['pagination']['agent_pages_used']=rounds
  aggregate['stop_reason']=stop
  if cursor:aggregate['coverage']['truncated']=True
  if cursor and aggregate['status']!='insufficient':aggregate['status']='partial'
  focus=re.findall(r'HBM\w*|SOCAMM\w*|CoWoS(?:-[SRL])?|\b9650\b|H200|Vera|Rubin|CUDA|SoIC|3DFabric',aggregate['retrieval_query'],re.I)
  state={'company':symbol,'focus_terms':list(dict.fromkeys(focus))[:12],'citations':aggregate['citations'],'known_entity_ids':sorted(entities),'turns':previous['turns']+1,
   'temporal_mode':aggregate['temporal_mode'],'analysis_time':aggregate['analysis_time'],'data_fingerprint':aggregate['fingerprints']['eligible_data'],
   'gaps':aggregate['unknowns'],'updated_at':now()}
  conn.execute('INSERT OR REPLACE INTO research_sessions_v2 VALUES (?,?)',(session_id,json.dumps(state,ensure_ascii=False)))
 result={'company_graph':{'paths':aggregate['paths'],'company_profile':aggregate['company_profile'],'pagination':aggregate['pagination']},
  'scenario':{'impact_paths':[p for p in aggregate['paths'] if p['path_type']=='conditional_inference_or_view']} if intent['scenario'] else None,
  'news':news,'expanded_entities':[]}
 final={'ticker':symbol,'question':question,'as_of':as_of,'route':intent,'agent_mode':'bounded_deterministic_unified_research_v2',
  'llm_generation_executed':False,'backend':backend,'rounds_used':rounds,'max_rounds':max_rounds,'stop_reason':stop,
  'session_id':session_id,'turn':state['turns'],'reused_valid_evidence_count':reused,'new_entity_ids':sorted(entities-set(known)),
  'evidence_verified':len(refs),'result':result,'evidence_packet':aggregate,'trace':trace,'gaps':aggregate['unknowns'],
  'answer_contract':aggregate['answer_contract']}
 dump(ROOT/'output'/f'agent_last_{re.sub("[^A-Za-z0-9_-]","_",session_id)[:80]}.json',final)
 return final
