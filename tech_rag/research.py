"""Unified, read-only evidence packets for research agents. No LLM or trading dependency.

Public API: build_research_context(ticker, question, as_of, budget=None, ...).
All retrieval channels are filtered before ranking; graph reachability is not a
claim of procurement or causality. Packet status measures retrieval completeness,
not the truth/accuracy of a generated answer (none is generated here).
"""
from dataclasses import dataclass,asdict,fields
from collections import defaultdict,deque
from pathlib import Path
import hashlib,json,re
from common import ROOT,DATASET,load,readl,sha,parse_time
from aliases import resolve_company,expand_query
from lexical import KeywordIndex,freeze,eligible,tokenize,encode_cursor,decode_cursor
from provenance import build_links,location_key
from tools import CompanyTools,INVERSE
from retrieval_v3 import keyword_candidates,diversify,ALGORITHM
from retrieval_config import RetrievalConfig

@dataclass(frozen=True)
class ResearchBudget:
 max_results:int=16
 keyword_candidates:int=60
 dense_candidates:int=16
 max_hops:int=2
 max_paths:int=24
 max_graph_candidates:int=180
 max_expanded_entities:int=8
 max_total_chars:int=22000
 max_excerpt_chars:int=2200
 context_radius:int=1
 max_pages:int=3
 max_rounds:int=3
 max_tool_calls:int=12
 use_dense:bool=True
 @classmethod
 def from_value(cls,value=None):
  if isinstance(value,cls):value=asdict(value)
  value=value or {}
  unknown=set(value)-{f.name for f in fields(cls)}
  if unknown:raise ValueError('unknown research budget fields: '+','.join(sorted(unknown)))
  result=cls(**value)
  bounds={'max_results':(1,100),'keyword_candidates':(1,300),'dense_candidates':(1,50),'max_hops':(1,3),
   'max_paths':(1,100),'max_graph_candidates':(10,2000),'max_expanded_entities':(0,30),'max_total_chars':(200,100000),
   'max_excerpt_chars':(100,8000),'context_radius':(0,2),'max_pages':(1,10),'max_rounds':(1,5),'max_tool_calls':(1,30)}
  for k,(lo,hi) in bounds.items():
   v=getattr(result,k)
   if isinstance(v,bool) or not isinstance(v,int) or not lo<=v<=hi:raise ValueError(f'{k} must be {lo}..{hi}')
  if not isinstance(result.use_dense,bool):raise ValueError('use_dense must be boolean')
  return result

def question_route(question):
 mechanism=bool(re.search(r'如果|影响|受限|必然|一定|条件|shortage|impact|constraint|传导',question,re.I))
 upstream=bool(re.search(r'上下游|上游|下游|供应商|供货|合作|业务|经营分部|伙伴|供应链|suppl|business|customer|partner',question,re.I))
 details=bool(re.search(r'多少|百分比|占比|型号|规格|容量|带宽|pitch|IOPS|PCIe|reticle|SSD|SOCAMM|HBM4|CoWoS-[SRL]|3/5/7|技术资料|line width',question,re.I))
 return {'intent':'impact_mechanism' if mechanism else ('company_relationships' if upstream and not details else 'disclosure_detail'),
  'preferred_channels':['graph','keyword','reviewed_dense'] if upstream and not details else ['keyword','reviewed_dense','graph'],
  'needs_dynamic_news':bool(re.search(r'近期|订单|最新进展|产品进展|政策调整|recent|latest orders',question,re.I))}

def _canonical(rows,key):return sorted(rows,key=lambda r:r[key])

def technical_coverage(question,citations):
 requested=[];excluded=[]
 for match in re.finditer(r'HBM[0-9][A-Z0-9]*|SOCAMM[0-9]+|H[0-9]{3}|[0-9]+(?:GB|TB)|PCIe\s*[0-9.]+|CoWoS\s*-\s*[SRL]',question,re.I):
  prefix=re.split(r'[，。;；?？\n]',question[:match.start()])[-1][-40:]
  suffix=re.split(r'[，。;；?？\n]',question[match.end():])[0][:30]
  # Scope parsing only: preserve question and quoted source verbatim. A later
  # generation named as a comparison boundary is not required historical proof.
  exclusion=bool(re.search(r'排除|清除|不得引用|不要引用|不使用|而非|exclude|do not (?:cite|use)',prefix,re.I))
  boundary=bool(re.search(r'保持与|区分于|不混用|不得混用',prefix) and re.search(r'边界|区分|混用',suffix))
  (excluded if exclusion or boundary else requested).append(match.group())
 requested=list(dict.fromkeys(requested));unmatched=[]
 for token in requested:
  chars=re.sub(r'[^a-z0-9]','',token.lower())
  pattern=r'(?<![a-z0-9])'+r'[^a-z0-9]*'.join(chars)+r'(?![a-z0-9])'
  if not any(re.search(pattern,c['text'],re.I) for c in citations.values()):unmatched.append(token)
 return {'requested_technical_tokens':requested,'unmatched_technical_tokens':unmatched,'excluded_technical_tokens':list(dict.fromkeys(excluded)),
  'coverage_check':'deterministic explicit technical-token coverage; not a semantic answer-sufficiency model'}

class ResearchEngine:
 def __init__(self,backend='local',config=None,*,chunks=None,index_path=None,tools=None,retrieval_config=None,snapshot=None):
  from knowledge_versions import active_snapshot
  self.snapshot=snapshot or getattr(tools,'snapshot',None) or active_snapshot()
  self.retrieval_config=RetrievalConfig.from_value(retrieval_config)
  self.tools=tools or CompanyTools(backend,config,snapshot=self.snapshot);self.backend=backend;self.config=config
  if backend=='neo4j' and self.snapshot.generation!='legacy':raise ValueError('Neo4j generation not verified; select the local snapshot backend')
  if chunks is None and backend=='neo4j':
   from neo4j_store import connect
   driver,info=connect(config);chunks=[]
   try:
    with driver.session(database=info['database']) as s:
     for offset in range(0,20000,500):
      page=[json.loads(r['payload']) for r in s.run('MATCH (c:TechChunk {dataset_id:$dataset}) RETURN c.payload AS payload ORDER BY c.chunk_id SKIP $offset LIMIT 500',dataset=DATASET,offset=offset)]
      chunks.extend(page)
      if len(page)<500:break
     else:raise RuntimeError('Tech chunk backend scan cap reached')
   finally:driver.close()
  self.chunks=chunks if chunks is not None else readl(self.snapshot.file('chunks.jsonl'))
  self.index=KeywordIndex(index_path or ROOT/f'state/tech_lexical_{backend}_{self.snapshot.generation}.sqlite3',self.chunks)
  self.chunks_by_id=self.index.by_id
  self.links=build_links(chunks=self.chunks,evidence=list(self.tools.evidence.values()),relations=self.tools.relations)
  self.e2c=defaultdict(list);self.c2e=defaultdict(list);self.e2r=defaultdict(list)
  for link in self.links['evidence_chunk_links']:
   self.e2c[link['evidence_id']].append(link['chunk_id']);self.c2e[link['chunk_id']].append(link['evidence_id'])
  for r in self.tools.relations:
   for eid in r['evidence_ids']:self.e2r[eid].append(r['relation_id'])
  self.relations={r['relation_id']:r for r in self.tools.relations};self._dense=None;self._raw={};self._hashes={};self._retrieval_cache={}

 def fingerprint(self,at,historical):
  rs=[r for r in self.tools.relations if self.tools.eligible(r,at,historical)[0] and all(eligible(self.tools.evidence[eid],at,historical) for eid in r['evidence_ids'])]
  es=[e for e in self.tools.evidence.values() if eligible(e,at,historical)]
  _,(_,cs)=self.index.snapshot(at,historical)
  # Future-only entities/catalog metadata must not contaminate a historical query key.
  entities={r[k] for r in rs for k in ['subject_id','object_id']}
  return sha(json.dumps({'relations':_canonical(rs,'relation_id'),'evidence':_canonical(es,'evidence_id'),
   'chunks':cs,'entities':_canonical([self.tools.entities[x] for x in entities],'entity_id')},sort_keys=True,ensure_ascii=False))

 def dense_search(self,question,at,historical,top_k):
  if self._dense is None:
   from dense import DenseRetriever
   self._dense=DenseRetriever(self.backend,self.config,snapshot=self.snapshot)
  return self._dense.search(question,at,historical,top_k)

 def has_cached_cursor(self,cursor):
  """A frozen page can reuse candidates without another retrieval query."""
  return bool(cursor and decode_cursor(cursor).get('query_key') in self._retrieval_cache)

 def graph_search(self,ticker,question,at,historical,budget,seeds=None):
  rs=[r for r in self.tools.relations if self.tools.eligible(r,at,historical)[0] and all(eligible(self.tools.evidence[e],at,historical) for e in r['evidence_ids'])]
  adj=defaultdict(list);terms=set(tokenize(expand_query(question)))
  def score(r):
   hay=' '.join(str(r.get(k,'')) for k in ['subject_id','predicate','object_id','claim','business_scope','conditions'])
   hay+=' '+' '.join(self.tools.entities[x]['name'] for x in [r['subject_id'],r['object_id']])
   return len(terms&set(tokenize(hay)))
  for r in rs:
   adj[r['subject_id']].append((r,r['object_id'],False));adj[r['object_id']].append((r,r['subject_id'],True))
  for group in adj.values():group.sort(key=lambda x:(-score(x[0]),x[0]['relation_id']))
  allowed=set(adj);seeds=sorted({s for s in (seeds or []) if s in allowed and s!=ticker})[:budget.max_expanded_entities]
  q=deque([(ticker,[],[ticker])]+[(s,[],[s]) for s in seeds]);paths=[];expanded=set();capped=False
  while q:
   node,steps,nodes=q.popleft()
   if len(steps)>=budget.max_hops:continue
   if node not in expanded and node!=ticker and len(expanded)>=budget.max_expanded_entities:continue
   expanded.add(node)
   for r,target,inverse in adj[node]:
    if target in nodes:continue
    # No indiscriminate expansion: later steps must match the question, or be
    # disclosed supply/technology links from an already matched entity.
    if steps and not score(r) and r['category'] not in ['supply','technology']:continue
    step={'order':len(steps)+1,'from_id':node,'to_id':target,'relation_id':r['relation_id'],
     'assertion_subject':r['subject_id'],'assertion_predicate':r['predicate'],'assertion_object':r['object_id'],
     'display_relation':INVERSE.get(r['predicate'],'INVERSE_OF_'+r['predicate']) if inverse else r['predicate'],
     'traversal_direction':'against_assertion' if inverse else 'with_assertion','evidence_class':r['evidence_class'],
     'directness':r['directness'],'category':r['category'],'impact_direction':r['direction'],
     'business_scope':r['business_scope'],'conditions':r['conditions'],'claim':r['claim'],'review_status':r['review_status'],
     'available_at':r['available_at'],'valid_from':r['valid_from'],'valid_to':r['valid_to'],'evidence_ids':r['evidence_ids']}
    seq=steps+[step];pathscore=sum(score(self.relations[s['relation_id']]) for s in seq)/len(seq)+1/len(seq)
    paths.append({'path_id':'RP_'+sha('|'.join(s['relation_id']+s['traversal_direction'] for s in seq))[:20],
     'nodes':nodes+[target],'steps':seq,'hops':len(seq),'ranking_score':pathscore,
     'path_type':'conditional_inference_or_view' if any(s['evidence_class']!='original_disclosure' for s in seq) else 'disclosed_relation_path',
     'causal_effect_estimated':False,'procurement_implied_by_reachability':False})
    if len(paths)>=budget.max_graph_candidates:capped=True;break
    if score(r)>0 or not steps:q.append((target,seq,nodes+[target]))
   if capped:break
  paths.sort(key=lambda p:(-p['ranking_score'],p['hops'],p['path_id']))
  return {'paths':paths,'eligible_relations':len(rs),'expanded_entity_ids':sorted(expanded),
   'enumeration_truncated':capped,'exhaustive':False,'seed_policy':'curated endpoints and exact named tokens only'}

 def verify_chunk(self,c):
  s=self.tools.sources.get(c['source_id'])
  if not s or not s.get('text_path') or not s.get('local_path'):return False,'original_missing'
  raw=self.snapshot.root/s['local_path'];text=self.snapshot.root/s['text_path']
  if not raw.is_file() or not text.is_file():return False,'original_missing'
  st=raw.stat();key=(str(raw),st.st_size,st.st_mtime_ns)
  if key not in self._hashes:
   with raw.open('rb') as f:self._hashes[key]=hashlib.file_digest(f,'sha256').hexdigest()
  if self._hashes[key]!=c['source_sha256']:return False,'original_version_hash_mismatch'
  ts=text.stat();tk=(str(text),ts.st_size,ts.st_mtime_ns)
  if tk not in self._raw:self._raw[tk]=readl(text)
  for block in self._raw[tk]:
   if block.get('pdf_page')==c.get('pdf_page') and block.get('paragraph')==c.get('paragraph'):
    normalized=re.sub(r'\s+',' ',block['text']).strip()
    if normalized[c['char_start']:c['char_end']]==c['text']:return True,None
  return False,'text_coordinate_round_trip_failed'

 def assemble(self,citations,at,historical,b,symbol,graph_paths):
  """Reconcile unioned citations; a partial page can never erase earlier coverage."""
  evidence_map={};assertions={};paths=[];categories=defaultdict(list)
  text_chars=sum(len(c['text']) for c in citations.values())
  visible={eid for c in citations.values() for eid in c['overlapping_reviewed_evidence_ids']}
  for eid in sorted(visible):
   e=self.tools.evidence[eid]
   if not eligible(e,at,historical):continue
   refs=[ref for ref,c in citations.items() if eid in c['overlapping_reviewed_evidence_ids']
         and location_key(c)==location_key(e)]
   ranges=sorted((max(citations[x]['char_start'],e['char_start']),min(citations[x]['char_end'],e['char_end'])) for x in refs)
   position=e['char_start']
   for lo,hi in ranges:
    if lo<=position:position=max(position,hi)
   # Independently round-trip the curated quote as well as each raw chunk.
   source=self.tools.sources[e['source_id']]
   blocks=readl(self.snapshot.root/source['text_path'])
   quote_ok=any(re.sub(r'\s+',' ',block['text']).strip()[e['char_start']:e['char_end']]==e['quote']
      for block in blocks if block.get('pdf_page')==e.get('pdf_page') and block.get('paragraph')==e.get('paragraph'))
   evidence_map[eid]={k:e.get(k) for k in ['evidence_id','source_id','source_sha256','pdf_page','paragraph','char_start','char_end','review_status']}
   evidence_map[eid].update(citation_ids=refs,fully_covered=position>=e['char_end'] and quote_ok,quote_round_trip_verified=quote_ok)
  relation_ids={rid for eid in visible for rid in self.e2r[eid]}
  omitted=0
  for rid in sorted(relation_ids):
   r=self.relations[rid]
   if not self.tools.eligible(r,at,historical)[0]:continue
   if not all(eid in evidence_map and evidence_map[eid]['fully_covered'] for eid in r['evidence_ids']):continue
   cost=sum(len(r[k]) for k in ['claim','conditions','business_scope'])
   if text_chars+cost>b.max_total_chars:omitted+=1;continue
   refs=sorted({ref for eid in r['evidence_ids'] for ref in evidence_map[eid]['citation_ids']})
   assertions[rid]={**r,'citation_ids':refs};text_chars+=cost
   if symbol in [r['subject_id'],r['object_id']]:
    name='constraints' if r['predicate'] in ['CONSTRAINED_BY','REPORTS_OUTLOOK','EXPOSED_TO'] else (
      'main_business' if r['category']=='business' else (
      'direct_upstream' if r['directness'].startswith('direct_') and r['object_id']==symbol else (
      'direct_downstream' if r['directness'].startswith('direct_') and r['subject_id']==symbol else 'indirect_dependencies_and_alternatives')))
    categories[name].append(rid)
  eligible_paths=[]
  for p in graph_paths:
   if not all(s['relation_id'] in assertions for s in p['steps']):continue
   steps=[{k:v for k,v in s.items() if k not in ['claim','conditions','business_scope']}|
     {'citation_ids':assertions[s['relation_id']]['citation_ids'],'assertion_ref':s['relation_id']} for s in p['steps']]
   eligible_paths.append({**p,'steps':steps})
  paths=eligible_paths[:b.max_paths]
  profile={k:categories.get(k,[]) for k in ['main_business','direct_upstream','direct_downstream','indirect_dependencies_and_alternatives','constraints']}
  return {'assertion_map':assertions,'evidence_map':evidence_map,'paths':paths,'company_profile':profile,
   'text_chars':text_chars,'assertions_omitted_by_budget':omitted,'paths_omitted_by_budget':max(0,len(eligible_paths)-b.max_paths)}

 def build(self,ticker,question,as_of,budget=None,*,cursor=None,mode='unified',session_context=None,temporal_context=None):
  b=ResearchBudget.from_value(budget);identity=resolve_company(ticker);symbol=identity['research_symbol']
  if not isinstance(question,str) or not question.strip() or len(question)>8000:raise ValueError('question must be 1..8000 characters')
  if mode not in ['unified','graph','dense','keyword']:raise ValueError('unsupported retrieval mode')
  at,historical=freeze(as_of);offset=0;page=1;previous_ids=[]
  if temporal_context is not None:
   if not isinstance(temporal_context,dict) or set(temporal_context)!={'analysis_time','historical'}:
    raise ValueError('temporal_context requires analysis_time and historical only')
   if not isinstance(temporal_context['historical'],bool) or not isinstance(temporal_context['analysis_time'],str):
    raise ValueError('temporal_context requires timezone ISO time and boolean historical')
   frozen_at=parse_time(temporal_context['analysis_time']);frozen_historical=temporal_context['historical']
   if historical:
    if not frozen_historical or frozen_at!=at:raise ValueError('temporal_context must match explicit historical as_of')
   elif frozen_historical or frozen_at>at:raise ValueError('current temporal_context must be nonhistorical and not in the future')
   at,historical=frozen_at,frozen_historical
  if cursor:
   cur=decode_cursor(cursor)
   if temporal_context is not None and (parse_time(cur['analysis_time'])!=at or cur['historical']!=historical):
    raise ValueError('cursor temporal_context mismatch')
   at=parse_time(cur['analysis_time']);historical=cur['historical']
   if as_of!='current' and (not historical or at!=parse_time(as_of)):raise ValueError('cursor analysis time mismatch')
   offset=cur['offset'];page=cur['page'];previous_ids=cur['citation_ids']
   if not isinstance(offset,int) or offset<0 or not isinstance(page,int) or page<1:raise ValueError('invalid research cursor')
   session_context=cur.get('session_context') or session_context
  fp=self.fingerprint(at,historical);route=question_route(question)
  memory_chunks=[];memory_seeds=[];retrieval_query=question;accepted_memory=None
  if session_context and session_context.get('data_fingerprint')==fp and session_context.get('company')==symbol:
   for cid in session_context.get('chunk_ids',[])[:100]:
    c=self.chunks_by_id.get(cid)
    if c and eligible(c,at,historical) and self.verify_chunk(c)[0]:memory_chunks.append(cid)
   # Follow-up ellipsis uses explicit prior technical tokens, never inferred identity.
   focus=session_context.get('focus_terms',[])[:12]
   if memory_chunks and re.search(r'该|上述|这个|它|继续|前面|that|these|this product',question,re.I):
    retrieval_query=question+' '+' '.join(focus)
   memory_seeds=session_context.get('seed_entity_ids',[])[:b.max_expanded_entities]
   accepted_memory={**session_context,'chunk_ids':memory_chunks,'focus_terms':focus,'seed_entity_ids':memory_seeds}
  key=sha(json.dumps([ALGORITHM,self.retrieval_config.version,symbol,question,retrieval_query,memory_chunks,at.isoformat(),historical,asdict(b),mode,fp],sort_keys=True,ensure_ascii=False))
  if cursor and cur.get('query_key')!=key:raise ValueError('research cursor/query/budget/data version mismatch')
  trace=[];keyword=None;dense=None;graph=None;calls=0;executed_channels=set()
  allowed_evidence={eid for eid,e in self.tools.evidence.items() if eligible(e,at,historical)}
  allowed_relations={rid for rid,r in self.relations.items() if self.tools.eligible(r,at,historical)[0] and set(r['evidence_ids'])<=allowed_evidence}
  def run(channel):
   nonlocal calls,keyword,dense,graph
   if calls>=b.max_tool_calls:return
   calls+=1;executed_channels.add(channel)
   if channel=='keyword':
    reserved=sum(ch not in executed_channels and (ch!='reviewed_dense' or b.use_dense) for ch in channels)
    keyword=keyword_candidates(self.index,retrieval_query,symbol,at,historical,b.keyword_candidates,
     max(1,b.max_tool_calls-calls+1-reserved),config=self.retrieval_config)
    calls+=keyword['query_calls']-1
    trace.append({'tool':'keyword_search','eligible_chunks':keyword['eligible_chunks'],'hits':len(keyword['hits']),
     'statistics_scope':keyword['statistics_scope'],'queries':keyword['queries'],
     'candidate_budget_allocated':keyword['candidate_budget_allocated'],'source_policy':keyword['source_policy']})
   elif channel=='reviewed_dense':
    dense=self.dense_search(retrieval_query,at,historical,b.dense_candidates)
    trace.append({'tool':'reviewed_dense_search','hits':len(dense['hits']),'cache_hit':dense['cache_hit'],'prefiltered_before_ranking':True})
   elif channel=='graph':
    seeds=list(memory_seeds)
    if dense:
     for h in dense['hits'][:6]:
      for rid in h.get('relation_ids',[]):
       if rid in allowed_relations:seeds.extend([self.relations[rid]['subject_id'],self.relations[rid]['object_id']])
    if keyword:
     for h in keyword['hits'][:6]:
      for eid in self.c2e[h['chunk_id']]:
       if eid not in allowed_evidence:continue
       for rid in self.e2r[eid]:
        if rid in allowed_relations:seeds.extend([self.relations[rid]['subject_id'],self.relations[rid]['object_id']])
    graph=self.graph_search(symbol,retrieval_query,at,historical,b,seeds)
    trace.append({'tool':'typed_graph_search','enumerated_paths':len(graph['paths']),'expanded_entity_ids':graph['expanded_entity_ids'],
     'max_hops':b.max_hops,'enumeration_truncated':graph['enumeration_truncated']})
  channels=route['preferred_channels'] if mode=='unified' else ([mode] if mode in ('graph','keyword') else ['reviewed_dense'])
  channels=[ch for ch in channels if ch in self.retrieval_config.channels]
  cached=self._retrieval_cache.get(key)
  if cached:
   keyword,dense,graph,executed_channels=cached
   trace.append({'tool':'reuse_frozen_candidate_snapshot','query_key':key,'original_candidate_queries_reexecuted':False,
    'eligible_time_and_fingerprint_rechecked':True})
  else:
   for ch in channels:
    if ch=='reviewed_dense' and not b.use_dense:continue
    run(ch)
  if not cached and graph and keyword and channels[0]=='graph' and calls<b.max_tool_calls:
   new_endpoints=set()
   for h in keyword['hits'][:6]:
    for eid in self.c2e[h['chunk_id']]:
     if eid not in allowed_evidence:continue
     for rid in self.e2r[eid]:
      if rid in allowed_relations:new_endpoints.update([self.relations[rid]['subject_id'],self.relations[rid]['object_id']])
   if new_endpoints-set(graph['expanded_entity_ids']):
    run('graph');trace[-1]['trigger']='new_exact_evidence_endpoints'
  if not cached:
   if len(self._retrieval_cache)>=16:self._retrieval_cache.pop(next(iter(self._retrieval_cache)))
   self._retrieval_cache[key]=(keyword,dense,graph,set(executed_channels))
  ranks=defaultdict(dict);raw_scores=defaultdict(dict);relation_candidates=set();evidence_candidates=set()
  query_terms=set(tokenize(expand_query(retrieval_query)))
  for rank,cid in enumerate(memory_chunks,1):
   if query_terms & set(tokenize(self.chunks_by_id[cid]['text'])):ranks[cid]['session']=rank
  if memory_chunks:trace.insert(0,{'tool':'reuse_verified_context','verified_chunk_ids':memory_chunks,
    'seed_entity_ids':memory_seeds,'focus_terms_used':accepted_memory['focus_terms'] if retrieval_query!=question else [],'original_locators_rechecked':True})
  if keyword:
   for h in keyword['hits']:
    ranks[h['chunk_id']]['keyword']=h['rank'];raw_scores[h['chunk_id']]['lexical_bm25']=h['lexical_bm25']
  if dense:
   for h in dense['hits']:
    relation_candidates.update(h['relation_ids']);evidence_candidates.update(h['evidence_ids'])
    for eid in h['evidence_ids']:
     for cid in self.e2c[eid]:
      ranks[cid].setdefault('reviewed_dense',h['rank']);raw_scores[cid]['dense_cosine']=max(h['score'],raw_scores[cid].get('dense_cosine',-1))
  if graph:
   eids=[]
   for p in graph['paths']:
    for s in p['steps']:
     relation_candidates.add(s['relation_id']);eids.extend(s['evidence_ids'])
   evidence_candidates.update(eids)
   for rank,eid in enumerate(dict.fromkeys(eids),1):
    for cid in self.e2c[eid]:ranks[cid].setdefault('graph',rank)
  # All channels normalize onto exact original chunks; dedup does not invent equivalence.
  weights=self.retrieval_config.weights(route['intent'])
  fused=[]
  for cid,channel_ranks in ranks.items():
   c=self.chunks_by_id[cid]
   if not eligible(c,at,historical):continue
   score=sum(weights[ch]/(self.retrieval_config.rrf_k+rank) for ch,rank in channel_ranks.items())
   fused.append((cid,score))
  fused.sort(key=lambda x:(-x[1],x[0]))
  if mode=='unified' and self.retrieval_config.diversify:fused=diversify(fused,self.chunks_by_id,retrieval_query,keyword,self.retrieval_config)
  self.last_ranking_audit={'config_version':self.retrieval_config.version,'config':asdict(self.retrieval_config),
   'candidates':[{'chunk_id':cid,'score':score,'channel_ranks':ranks[cid]} for cid,score in fused]}
  citations={};selected=[];rejected=[];text_chars=0;processed=offset;context_ids=[]
  def add_chunk(cid,channel='retrieved',limit=None):
   nonlocal text_chars
   c=self.chunks_by_id[cid]
   if not eligible(c,at,historical):return None
   valid,reason=self.verify_chunk(c)
   if not valid:rejected.append({'chunk_id':cid,'reason':reason});return None
   start=c['char_start'];available=min(b.max_excerpt_chars,len(c['text']),b.max_total_chars-text_chars)
   if limit is not None:available=min(available,limit)
   if available<min(80,len(c['text'])):return None
   text=c['text'][:available];end=start+len(text)
   citation_id='C_'+sha(json.dumps([c['source_id'],c['source_sha256'],c.get('pdf_page'),c.get('paragraph'),start,end]))[:24]
   if citation_id in citations or citation_id in previous_ids:return None
   exact_evidence=[eid for eid in self.c2e[cid] if eligible(self.tools.evidence[eid],at,historical) and self.tools.evidence[eid]['char_start']<end and self.tools.evidence[eid]['char_end']>start]
   source=self.tools.sources[c['source_id']]
   citations[citation_id]={'knowledge_generation':self.snapshot.generation,'citation_id':citation_id,'chunk_id':cid,'source_id':c['source_id'],'source_sha256':c['source_sha256'],
    'source_url':c['source_url'],'title':source['title'],'report_period':c.get('report_period'),
    'published_at':source.get('published_at'),'actual_public_at':source.get('actual_public_at'),
    'availability_basis':source.get('availability_basis'),'available_at':c['available_at'],'historical_eligible':c['historical_eligible'],
    'pdf_page':c.get('pdf_page'),'paragraph':c.get('paragraph'),'char_start':start,'char_end':end,'text':text,
    'coordinate_system':'normalized whitespace Unicode, half-open [start,end)','section':c.get('section'),
    'table_policy':c.get('table_policy'),'round_trip_verified':True,'review_status':'raw_original_unreviewed_as_relation',
    'overlapping_reviewed_evidence_ids':exact_evidence,'retrieval_role':channel,
    'excerpt_truncated':available<len(c['text']),'local_original':str(self.snapshot.root/source['local_path'])}
   text_chars+=len(text);return citation_id
  for pos,(cid,score) in enumerate(fused[offset:],offset):
   if len(selected)>=b.max_results:break
   processed=pos+1
   citation_id=add_chunk(cid)
   if citation_id:
    selected.append({'citation_id':citation_id,'chunk_id':cid,'fusion_score':score,'channel_ranks':ranks[cid],
     'raw_scores':raw_scores[cid],'score_meaning':'relevance_only','rank':pos+1})
   if b.max_total_chars-text_chars<80:break
  # Nearby context is optional and never displaces the selected page. It remains
  # same source hash + physical page/paragraph, eligible, and shares the text cap.
  if b.context_radius and not cursor:
   for h in list(selected):
    c=self.chunks_by_id[h['chunk_id']]
    neighbours=sorted([x for x in self.chunks if location_key(x)==location_key(c) and x['chunk_id']!=c['chunk_id'] and eligible(x,at,historical)],key=lambda x:abs(x['char_start']-c['char_start']))[:b.context_radius]
    for n in neighbours:
     if n['chunk_id'] in {x['chunk_id'] for x in citations.values()}:continue
     if len(citations)>=b.max_results+b.context_radius:break
     ref=add_chunk(n['chunk_id'],'nearby_same_version_context',limit=900)
     if ref:context_ids.append(ref)
  self._last_graph_paths=graph['paths'] if graph else []
  assembled=self.assemble(citations,at,historical,b,symbol,self._last_graph_paths)
  assertions=assembled['assertion_map'];evidence_map=assembled['evidence_map'];paths=assembled['paths'];text_chars=assembled['text_chars']
  category=assembled['company_profile']
  gaps=['当前语料只深度覆盖MU、TSM、NVDA；未发现不代表未披露。','未披露的供应份额、客户身份、订单与价格条款不可推断。']
  insuff=[]
  if re.search(r'股价|收益|stock price|return',question,re.I) and re.search(r'确定|多少|百分比|exact|predict',question,re.I):insuff.append('缺少已识别的市场收益因果模型、冲击基数和估值参数，不能给出确定收益。')
  if re.search(r'份额|占比',question) and re.search(r'采购|供应商|供应|供货',question) and re.search(r'精确|百分比|多少|其他两家|分别.*占比',question):insuff.append('本页具名供应披露未核实产品采购份额；不得以存在供货关系估计百分比。')
  if re.search(r'Customer\s*[AB]|客户[ABC]|匿名',question,re.I) and re.search(r'哪家|谁|微软|亚马逊|苹果|英伟达|身份',question):insuff.append('匿名客户身份未披露，不能按收入占比或行业相关性实名。')
  if re.search(r'48\s*GB',question,re.I) and re.search(r'完整.*名单|所有.*NVIDIA|全部.*客户',question,re.I):insuff.append('48GB送样对象只称customers，不能借36GB产品关系识别。')
  if re.search(r'供应商|supplier',question,re.I) and re.search(r'每一家|每家|完整.*清单|全部|every|complete',question,re.I) and re.search(r'真实名称|对应物料|承诺.*数量|合同单价|contract price|committed quantit',question,re.I):
   insuff.append('本页原文未逐家核实供应商身份、物料映射及合同数量/单价；通用单一来源或长约风险不能支持完整明细，不能将缺失条款解释为零。')
  if re.search(r'一家|某家|某个|匿名|未具名|unnamed|\bone\b.*\bcompany\b',question,re.I) and re.search(r'是否.*(?:就是|为)|哪家|是谁|身份|确认.*(?:公司|企业)|identity|which company',question,re.I):
   insuff.append('本页没有可核实的匿名/不具名描述到具名企业的身份映射；不能凭行业相关性或收入描述确认身份，也不能据此给出该企业精确贡献额或营收比例。')
  question_coverage=technical_coverage(retrieval_query,citations);unmatched=question_coverage['unmatched_technical_tokens']
  if unmatched:insuff.append('本页可回查原文尚未覆盖指定型号/数值标识：'+', '.join(unmatched)+'；需继续分页或补查，不能以相近产品推断。')
  if not citations:insuff.append('本页没有可定位、通过时间与版本验证的正文；存在检索预算或资料缺口。')
  if historical:gaps.append('actual_public_at尚未逐笔独立核验；采用目录保守available_at，不能作为精确盘中回测时间。')
  remaining=processed<len(fused)
  cap_flags={'result_page':remaining,'text_budget':text_chars>=b.max_total_chars-80,'graph_enumeration':bool(graph and graph['enumeration_truncated']),
   'tool_calls':bool(set(ch for ch in channels if ch!='reviewed_dense' or b.use_dense)-executed_channels),
   'path_limit':assembled['paths_omitted_by_budget']>0,'assertion_text_budget':assembled['assertions_omitted_by_budget']>0,'candidate_limit':bool(keyword and keyword['truncated'])}
  page_limit=page>=b.max_pages
  next_cursor=encode_cursor({'generation':self.snapshot.generation,'query_key':key,'offset':processed,'page':page+1,'analysis_time':at.isoformat(),'historical':historical,
   'citation_ids':list(dict.fromkeys(previous_ids+list(citations))),'session_context':accepted_memory}) if remaining and not page_limit else None
  trace.append({'tool':'original_locator_verification','verified_chunks':len(citations),'rejected':rejected,'partial_evidence_spans':sum(not x['fully_covered'] for x in evidence_map.values())})
  conflicts=[]
  # Detect value/wording differences only for the same declared triple/scope and
  # preserve periods. Different periods are comparisons, not contradictions.
  groups=defaultdict(list)
  for r in assertions.values():groups[(r['subject_id'],r['predicate'],r['object_id'],r['business_scope'])].append(r)
  for group in groups.values():
   if len({r['claim'] for r in group})>1:
    conflicts.append({'kind':'wording_difference_requires_review','relation_ids':[r['relation_id'] for r in group],
     'resolution':'未自动判定矛盾；先核对口径、报告期与资料版本。'})
  model=load(self.snapshot.file('model_provenance.json'))
  return {'schema_version':'tech_evidence_packet_v2','dataset_id':DATASET,**identity,'question':question,
   'analysis_time':at.isoformat(),'as_of_input':as_of,'temporal_mode':'strict_historical' if historical else 'current_as_collected','retrieval_query':retrieval_query,
   'backend':self.backend,'route':route,'retrieval_mode':mode,
   'status':'insufficient' if insuff else ('partial' if remaining or any(cap_flags.values()) else 'evidence_found'),
   'insufficiency_reasons':insuff,'results':selected,'citations':citations,'evidence_map':evidence_map,'assertion_map':assertions,
   'question_coverage':question_coverage,
   'company_profile':{k:category.get(k,[]) for k in ['main_business','direct_upstream','direct_downstream','indirect_dependencies_and_alternatives','constraints']},
   'paths':paths,'conflicts':conflicts,'conflict_check_scope':'same triple/scope wording comparison; no exhaustive semantic contradiction audit',
   'unknowns':gaps,'suggested_followups':['取得未披露采购/订单的正式公告或明确回复','核验实际首次公开时间与历史原始版本'],
   'pagination':{'page':page,'offset':offset,'returned':len(selected),'ranked_candidates':len(fused),'next_cursor':next_cursor,
    'resume_available':next_cursor is not None,'max_pages':b.max_pages,'page_limit_reached':page_limit and remaining},
   'coverage':{'core_companies':['MU','TSM','NVDA'],'eligible_original_chunks':len(self.index.snapshot(at,historical)[1][1]),
    'eligible_relations':graph['eligible_relations'] if graph else None,'truncated':any(cap_flags.values()),'truncation':cap_flags,
    'context_citation_ids':context_ids,'exhaustive':False,'raw_hits_are_reviewed_relations':False},
   'budget':asdict(b),'budget_usage':{'text_chars':text_chars,'text_scope':'unique excerpt text + unique assertion claim/condition/business_scope; excludes JSON keys, IDs, audit metadata',
    'retrieval_tool_calls':calls,'locator_verification_mandatory':True},
   'stop_reason':'insufficient_evidence' if insuff else ('page_limit' if page_limit and remaining else ('text_budget' if cap_flags['text_budget'] else ('page_size' if remaining else ('tool_budget' if cap_flags['tool_calls'] else 'candidate_set_exhausted')))),
   'fingerprints':{'knowledge_generation':self.snapshot.generation,'retrieval_config_version':self.retrieval_config.version,'eligible_data':fp,'eligible_keyword_corpus':self.index.snapshot(at,historical)[0],
    'embedding_signature':sha(json.dumps(model,sort_keys=True)),'embedding_model':model['model'],'embedding_revision':model['revision'],
    'retrieval_algorithm':ALGORITHM,'fusion_weights':weights},
   'trace':trace,'answer_contract':'研究证据包；相关性不代表可信度，图上可达不代表交易关系或因果，禁止实名匿名客户及确定收益外推。'}

_engines={}
def build_research_context(ticker,question,as_of,budget=None,*,backend='local',config=None,cursor=None,temporal_context=None):
 """Return a JSON-serializable EvidencePacket; explicit as_of requires timezone.

 Use a ResearchEngine instance for a multi-call session. This convenience API
 reloads if local artifacts change; Neo4j always constructs a fresh snapshot.
 """
 from knowledge_versions import active_snapshot
 generation=decode_cursor(cursor).get('generation','legacy') if cursor else None
 snapshot=active_snapshot(generation=generation)
 stat=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in [snapshot.file(n) for n in ('chunks.jsonl','relations.json','evidence.json','source_catalog.json','entities.json')])
 key=(backend,str(config),snapshot.generation,stat)
 if backend=='neo4j' or key not in _engines:_engines[key]=ResearchEngine(backend,config,snapshot=snapshot)
 return _engines[key].build(ticker,question,as_of,budget,cursor=cursor,temporal_context=temporal_context)
