"""Company-first, typed and time-filtered graph/evidence tools. Offline mode is explicit."""
from common import *
from collections import defaultdict,Counter,deque
import base64,re

INVERSE={'SUPPLIES_TO':'SUPPLIED_BY','MANUFACTURES_FOR':'MANUFACTURED_BY','ASSEMBLES_FOR':'ASSEMBLED_BY',
 'HAS_BUSINESS':'BUSINESS_OF','OFFERS':'OFFERED_BY','SERVES':'CUSTOMER_OF','ANNOUNCED_FOR':'ANNOUNCED_COMPONENT_FROM',
 'DESIGNED_FOR':'DESIGNED_COMPONENT_FROM','MANUFACTURED_USING':'PROCESS_USED_BY'}

class CompanyTools:
 def __init__(self,backend='local',config=None,*,snapshot=None):
  from knowledge_versions import active_snapshot
  self.snapshot=snapshot or active_snapshot()
  artifact=self.snapshot.file
  if backend not in ['local','neo4j']:raise ValueError('backend must be local or neo4j')
  self.backend=backend;self.config=config
  if backend=='neo4j':
   from neo4j_store import read_dataset
   data=read_dataset(config)
   self.entities={r['entity_id']:r for r in data['entities']};self.relations=data['relations']
   self.evidence={r['evidence_id']:r for r in data['evidence']};self.sources={r['source_id']:r for r in data['sources']}
  else:
   self.entities={r['entity_id']:r for r in load(artifact('entities.json'))};self.relations=load(artifact('relations.json'))
   self.evidence={r['evidence_id']:r for r in load(artifact('evidence.json'))};self.sources={r['source_id']:r for r in load(artifact('source_catalog.json'))}
  self.fingerprint=sha(json.dumps({'r':self.relations,'e':list(self.evidence.values())},sort_keys=True,ensure_ascii=False))
  self._verified_source_hashes={}

 def resolve_time(self,as_of):
  if not as_of:raise ValueError('as_of is required; use explicit timezone or current')
  return (parse_time(now()),False) if as_of=='current' else (parse_time(as_of),True)

 def eligible(self,r,at,historical):
  if parse_time(r['available_at'])>at:return False,'not_public_at_analysis_time'
  if historical and not r.get('historical_eligible'):return False,'historical_version_or_derived_time_unverified'
  if r.get('valid_from') and at.date().isoformat()<r['valid_from']:return False,'not_effective'
  if r.get('valid_to') and at.date().isoformat()>r['valid_to']:return False,'outside_effective_interval'
  return True,None

 def company_relations(self,ticker,as_of,question='',page_size=20,cursor=None,max_hops=3,max_paths=2000):
  ticker=ticker.upper();at,historical=self.resolve_time(as_of)
  if cursor and as_of=='current':
   try:
    frozen=json.loads(base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4)))
    at=parse_time(frozen['resolved_analysis_time'])
   except Exception:raise ValueError('invalid or unfrozen current-time cursor')
  if ticker not in ['MU','TSM','NVDA']:raise ValueError('v1 core company must be MU, TSM or NVDA; related entities use expand_entity')
  if not 1<=page_size<=100 or not 1<=max_hops<=4 or not 10<=max_paths<=10000:raise ValueError('invalid traversal or page bounds')
  rels=[];exclusions=Counter()
  for r in self.relations:
   ok,why=self.eligible(r,at,historical)
   if ok:rels.append(r)
   else:exclusions[why]+=1
  adjacency=defaultdict(list)
  for r in rels:
   adjacency[r['subject_id']].append((r,r['object_id'],False));adjacency[r['object_id']].append((r,r['subject_id'],True))
  for group in adjacency.values():group.sort(key=lambda x:x[0]['relation_id'])
  paths=[];queue=deque([(ticker,[],[ticker])]);capped=False
  qtokens=set(re.findall(r'[A-Za-z0-9]+|[\u3400-\u9fff]{2}',question.lower()))
  while queue:
   owner,steps,visited=queue.popleft()
   if len(steps)>=max_hops:continue
   for r,target,inverse in adjacency.get(owner,[]):
    if target in visited:continue
    evidence=[self.evidence[e] for e in r['evidence_ids']]
    step={'order':len(steps)+1,'from_id':owner,'from_name':self.entities[owner]['name'],
     'to_id':target,'to_name':self.entities[target]['name'],
     'display_relation':INVERSE.get(r['predicate'],'INVERSE_OF_'+r['predicate']) if inverse else r['predicate'],
     'assertion_subject':r['subject_id'],'assertion_predicate':r['predicate'],'assertion_object':r['object_id'],
     'traversal_direction':'against_assertion' if inverse else 'with_assertion','relation_id':r['relation_id'],
     'category':r['category'],'directness':r['directness'],'evidence_class':r['evidence_class'],
     'business_scope':r['business_scope'],'conditions':r['conditions'],'impact_direction':r['direction'],
     'valid_from':r['valid_from'],'valid_to':r['valid_to'],'available_at':r['available_at'],
     'status':r['status'],'claim':r['claim'],'evidence':evidence}
    sequence=steps+[step];nodes=visited+[target]
    hay=' '.join(s['from_name']+' '+s['to_name']+' '+s['claim']+' '+s['business_scope'] for s in sequence).lower()
    score=sum(token in hay for token in qtokens)*10+2/len(sequence)
    if any(s['category']=='impact_mechanism' for s in sequence) and question:score+=3
    paths.append({'path_id':'TP_'+sha('|'.join(s['relation_id']+s['traversal_direction'] for s in sequence))[:20],
     'nodes':nodes,'steps':sequence,'hops':len(sequence),'ranking_score':score,
     'path_type':'conditional_mechanism' if any(s['evidence_class']!='original_disclosure' for s in sequence) else 'disclosed_relation_path',
     'causal_effect_estimated':False})
    if len(paths)>=max_paths:capped=True;break
    queue.append((target,sequence,nodes))
   if capped:break
  paths.sort(key=lambda p:(-p['ranking_score'],p['hops'],p['path_id']))
  query_key=sha(json.dumps([ticker,as_of,question,page_size,max_hops,max_paths,self.fingerprint],ensure_ascii=False))
  offset=0
  if cursor:
   try:obj=json.loads(base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4)))
   except Exception:raise ValueError('invalid cursor')
   if obj['query_key']!=query_key:raise ValueError('cursor does not belong to this query or dataset version')
   offset=obj['offset']
   if not isinstance(offset,int) or not 0<=offset<=len(paths):raise ValueError('invalid cursor offset')
  page=paths[offset:offset+page_size];next_offset=offset+len(page)
  next_cursor=base64.urlsafe_b64encode(json.dumps({'offset':next_offset,'query_key':query_key,'resolved_analysis_time':at.isoformat()}).encode()).decode().rstrip('=') if next_offset<len(paths) else None
  one=[r for r in rels if ticker in [r['subject_id'],r['object_id']]]
  categories={}
  for key,predicates in [('main_business',['HAS_BUSINESS','OFFERS','MANUFACTURES']),
   ('technical_dependencies',['USES_TECHNOLOGY','DEPENDS_ON_INPUT','EXPOSED_TO','COLLABORATES_WITH','CO_DEVELOPS']),
   ('competition_and_alternatives',['COMPETES_WITH','FACES_ALTERNATIVE','HAS_POTENTIAL_ALTERNATIVE']),
   ('operating_constraints',['CONSTRAINED_BY','REPORTS_OUTLOOK'])]:
   categories[key]=[self.brief(r) for r in one if r['predicate'] in predicates]
  direct=[r for r in one if r['directness'].startswith('direct_')]
  # Business-map view shows the latest disclosed version per target; complete paths retain all evidence versions.
  latest={}
  for r in categories['main_business']:
   key=(r['subject_id'],r['predicate'],r['object_id'])
   if key not in latest or r['available_at']>latest[key]['available_at']:latest[key]=r
  categories['main_business']=sorted(latest.values(),key=lambda r:r['relation_id'])
  categories['direct_upstream']=[self.brief(r) for r in direct if r['object_id']==ticker and r['category']=='supply']
  categories['direct_downstream']=[self.brief(r) for r in direct if r['subject_id']==ticker and (r['category'] in ['supply','customer'])]
  shortages=[]
  if not categories['main_business']:shortages.append('此分析时点没有通过时间/版本校验的主要业务证据；不能用后来财报补齐。')
  if not categories['direct_upstream']:shortages.append('审核资料未在此时点识别直接上游公司；原料/技术依赖不能当成具名供货商。')
  shortages.extend(['供应份额、订单量、价格条款和排他性普遍未披露；匿名客户不识别。',
   '首版只深度整理MU、TSM、NVDA；关联公司并未完成全量产业链研究。',
   '政策只覆盖所列原文与日期，后续修订/许可证个案未完整核验。'])
  return {'ticker':ticker,'analysis_time':at.isoformat(),'as_of_input':as_of,'temporal_mode':'strict_historical' if historical else 'current_as_collected',
   'backend':self.backend,'dataset_fingerprint':self.fingerprint,**categories,'paths':page,
   'pagination':{'offset':offset,'page_size':page_size,'returned':len(page),'enumerated_paths':len(paths),'next_cursor':next_cursor},
   'coverage':{'core_companies':['MU','TSM','NVDA'],'eligible_relations':len(rels),'catalog_relations':len(self.relations),
    'excluded_by_reason':dict(exclusions),'max_hops':max_hops,'enumeration_limit':max_paths,'enumeration_truncated':capped,
    'page_truncated':next_cursor is not None,'total_paths_known_exactly':not capped},'gaps':shortages}

 def brief(self,r):
  return {**r,'subject_name':self.entities[r['subject_id']]['name'],'object_name':self.entities[r['object_id']]['name']}

 def evidence_lookup(self,evidence_ids,as_of):
  at,historical=self.resolve_time(as_of);items=[];missing=[]
  for eid in dict.fromkeys(evidence_ids):
   e=self.evidence.get(eid)
   if not e:missing.append({'evidence_id':eid,'reason':'unknown_id'});continue
   if parse_time(e['available_at'])>at or (historical and not e['historical_eligible']):
    missing.append({'evidence_id':eid,'reason':'not_available_or_historical_version_unverified'});continue
   source=self.sources[e['source_id']]
   if not source.get('text_path') or not (self.snapshot.root/source['text_path']).exists():
    missing.append({'evidence_id':eid,'reason':'local_original_text_missing'});continue
   raw=self.snapshot.root/source['local_path']
   if not raw.is_file():missing.append({'evidence_id':eid,'reason':'local_original_missing'});continue
   stat=raw.stat();cache_key=(str(raw),stat.st_size,stat.st_mtime_ns)
   if cache_key not in self._verified_source_hashes:
    import hashlib
    with raw.open('rb') as file:self._verified_source_hashes[cache_key]=hashlib.file_digest(file,'sha256').hexdigest()
   if self._verified_source_hashes[cache_key]!=e['source_sha256']:
    missing.append({'evidence_id':eid,'reason':'original_source_hash_mismatch'});continue
   rows=readl(self.snapshot.root/source['text_path'])
   match=[b for b in rows if b.get('pdf_page')==e['pdf_page'] and b.get('paragraph')==e['paragraph']]
   valid=any(re.sub(r'\s+',' ',b['text']).strip()[e['char_start']:e['char_end']]==e['quote'] for b in match)
   if not valid:missing.append({'evidence_id':eid,'reason':'locator_round_trip_failed'});continue
   items.append({**e,'round_trip_verified':True,'local_original':str(self.snapshot.root/source['local_path'])})
  return {'items':items,'missing':missing,'analysis_time':at.isoformat()}

 def expand_entity(self,entity_id,as_of,limit=20):
  if not 1<=limit<=100:raise ValueError('invalid expansion bound')
  at,historical=self.resolve_time(as_of)
  if entity_id not in self.entities:return {'entity_id':entity_id,'found':False,'relations':[],'gap':'entity not in reviewed graph'}
  found=[r for r in self.relations if entity_id in [r['subject_id'],r['object_id']] and self.eligible(r,at,historical)[0]]
  found.sort(key=lambda r:r['relation_id'])
  if not found:return {'entity_id':entity_id,'found':False,'relations':[],'total':0,'truncated':False,
   'gap':'该时点没有可用的审核关系；不返回后来披露的实体元数据。'}
  return {'entity':self.entities[entity_id],'found':True,'relations':[self.brief(r) for r in found[:limit]],'total':len(found),'truncated':len(found)>limit,'gap':None}

 def scenario(self,ticker,kind,as_of):
  if kind not in ['hbm_shortage','packaging_capacity']:raise ValueError('unsupported scenario')
  at,historical=self.resolve_time(as_of)
  # These are authored analytical rules; never backdate them into historical information sets.
  resource='HBM_SUPPLY' if kind=='hbm_shortage' else 'PACKAGING_SUPPLY'
  mechanisms=[r for r in self.relations if r['subject_id']==resource and r['category']=='impact_mechanism' and self.eligible(r,at,historical)[0]]
  paths=[]
  for r in mechanisms:
   if ticker=='MU' and r['object_id']!='MU':continue
   if ticker in ['NVDA','TSM'] and r['object_id']=='MU':continue
   steps=[{'order':1,'from_id':resource,'relation_id':r['relation_id'],'predicate':r['predicate'],
    'to_id':r['object_id'],'evidence_class':r['evidence_class'],'direction':r['direction'],
    'conditions':r['conditions'],'evidence_ids':r['evidence_ids']}]
   if r['object_id'] in ['H200','VERA_RUBIN']:
    product=next((x for x in self.relations if x['subject_id']=='NVDA' and x['predicate']=='OFFERS' and x['object_id']==r['object_id'] and self.eligible(x,at,historical)[0]),None)
    if product:steps.append({'order':2,'from_id':r['object_id'],'predicate':'PRODUCT_OF','to_id':'NVDA',
     'relation_id':product['relation_id'],'evidence_class':product['evidence_class'],'evidence_ids':product['evidence_ids'],
     'conditions':'产品归属，不是对利润或股价的因果估计。','direction':'not_applicable'})
   paths.append({'steps':steps,'source_company_query':ticker,'mechanism_target_company':'MU' if r['object_id']=='MU' else 'NVDA',
    'path_type':'conditional_model_inference','causal_effect_estimated':False})
  return {'ticker':ticker,'scenario':kind,'analysis_time':at.isoformat(),'impact_paths':paths,
   'coverage_note':'TSM查询可展示其技术生态下游NVDA暴露；不等于MU与TSM之间存在未披露采购关系。',
   'unknowns':['具体库存缓冲','供应商/封装线认证与切换时间','实际可用产能和良率','合同价格及交付窗口','其他零部件和电力瓶颈'],
   'insufficient':not paths,'gap':'该时点没有可用的审核推理规则；不以当前推断回填历史。' if not paths else None}

 def semantic_evidence(self,query,as_of,top_k=6):
  from dense import DenseRetriever
  at,historical=self.resolve_time(as_of)
  if not 1<=top_k<=50:raise ValueError('top_k must be 1..50')
  context={'analysis_time':at.isoformat(),'temporal_mode':'strict_historical' if historical else 'current_as_collected',
   'backend':self.backend,'requested_top_k':top_k}
  if not hasattr(self,'_dense_retriever'):self._dense_retriever=DenseRetriever(self.backend,self.config)
  result=self._dense_retriever.search(query,at,historical,top_k)
  return {**context,**result,'total_records':len(self._dense_retriever.records),'truncated':result['eligible_records']>top_k}

 def research_context(self,ticker,question,as_of,budget=None,cursor=None):
  from research import ResearchEngine
  if not hasattr(self,'_research_engine'):self._research_engine=ResearchEngine(self.backend,self.config,tools=self)
  return self._research_engine.build(ticker,question,as_of,budget,cursor=cursor)
