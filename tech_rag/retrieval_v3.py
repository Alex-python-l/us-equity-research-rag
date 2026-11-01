"""Fixed query facets and bounded original-source retrieval; no gold/corpus learning.

Every FTS query uses the same eligible snapshot/statistics. Facet quotas sum to
the existing keyword candidate budget. Ticker is a small score preference only.
"""
import re
from collections import defaultdict
from aliases import expand_query
from lexical import tokenize

ALGORITHM='eligible-FTS5 fixed bilingual facets + soft-source diversified RRF v4'


def query_facets(question):
 facets=[]
 def add(name,*groups):facets.append({'name':name,'groups':list(groups)})
 if re.search(r'供应商|供应伙伴|供货|晶圆|foundr|wafer|supplier|purchase|procure|伙伴',question,re.I):
  if re.search(r'内存|memory',question,re.I):add('memory_purchase',['memory'],['purchase','supplier'])
  if re.search(r'晶圆|foundr|wafer|供应伙伴',question,re.I):add('wafer_foundry',['foundry','foundries'],['wafer'])
  if re.search(r'组装|assembly',question,re.I):add('assembly_partners',['assembly'],['partner','subcontractor','testing'])
 if re.search(r'经营分部|业务单元|主要业务|经营业务|operating segment',question,re.I):add('operating_segments',['segment','unit'],['operating','business','compute','graphics','cloud'])
 if re.search(r'供应商|supplier',question,re.I):
  if re.search(r'长期合同|长约|合同|contract',question,re.I):add('supplier_contract',['contract'],['capacity','pricing','quantity','supply'])
  if re.search(r'单一来源|single|sole',question,re.I):add('single_source',['single','sole'],['source','supplier'])
 if re.search(r'CUDA',question,re.I):add('cuda_platform',['cuda'],['software','platform','programming'])
 if re.search(r'合作|collaborat',question,re.I):
  add('technology_collaboration',['collaborate','collaboration'],['technology','logic','process'])
  if re.search(r'逻辑|自制|manufactur|logic',question,re.I):add('manufacturing_scope',['logic'],['manufacture','in-house','die'])
 if re.search(r'匿名|Customer\s*[AB]|客户[AB]|unnamed',question,re.I):
  if re.search(r'Customer\s*[AB]|客户[AB]',question,re.I):add('customer_labels',['customer a','customer b','customer c'])
  add('customer_concentration',['customer'],['accounted','concentration','revenue','direct'])
 if re.search(r'间接客户|indirect customer|云服务|cloud services',question,re.I):
  add('indirect_customer',['customer'],['indirect','cloud'],['revenue','purchase'])
 if re.search(r'CoWoS',question,re.I):
  variants=re.findall(r'CoWoS\s*-\s*([SRL])',question,re.I)
  if re.search(r'变体|S、R、L|S.R.L|variants',question,re.I):variants=['S','R','L']
  for variant in dict.fromkeys(v.upper() for v in variants):add('cowos_'+variant.lower(),['cowos'],[variant],['interposer','interconnect','silicon','rdl'])
  if re.search(r'pitch|线宽|间距',question,re.I):add('interposer_pitch',['interposer','rdl'],['pitch','spacing'])
  if re.search(r'面积|reticle|上限',question,re.I):add('interposer_area',['cowos','interposer'],['reticle','size'])
 if re.search(r'3DFabric|SoIC',question,re.I):add('integration_platform',['3dfabric','soic'],['integration','platform','packaging'])
 # Combine related variants into one retrieval query when there are many
 # facets; all three retain independent selection coverage below.
 return facets


def _match(groups):
 expressions=[]
 for group in groups:
  terms=list(dict.fromkeys(' '.join(tokenize(word)) for word in group))
  if terms:expressions.append('('+' OR '.join('"'+t.replace('"','""')+'"' for t in terms)+')')
 return ' AND '.join(expressions)


def keyword_candidates(index,question,ticker,at,historical,limit,max_calls):
 fp,(db,rows)=index.snapshot(at,historical)
 facets=query_facets(question)
 # Each facet is an actual counted source query. Keep a slot for the full
 # question and respect even a one-call override without hidden searches.
 chosen=facets[:max(0,max_calls-1)]
 plans=[{'name':'whole_question','groups':[list(dict.fromkeys(tokenize(expand_query(question))))[:160]]}]+chosen
 plans=plans[:max(1,min(max_calls,limit))]
 base=max(1,limit//len(plans));quotas=[base]*len(plans)
 quotas[0]+=limit-sum(quotas)
 db.create_function('source_soft',1,lambda cid:1.25 if index.by_id[cid].get('ticker')==ticker else 1.0)
 merged={};total=0;queries=[]
 for plan,quota in zip(plans,quotas):
  match=_match(plan['groups'])
  matched=db.execute('SELECT count(*) FROM documents_fts WHERE documents_fts MATCH ?',(match,)).fetchone()[0] if match else 0
  total=max(total,matched)
  found=db.execute('SELECT id,bm25(documents_fts) AS bm FROM documents_fts WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts)*source_soft(id),id LIMIT ?',
   (match,quota)).fetchall() if matched else []
  queries.append({'facet':plan['name'],'quota':quota,'returned':len(found),'matched':matched})
  for rank,(cid,bm) in enumerate(found,1):
   item=merged.setdefault(cid,{**index.by_id[cid],'lexical_bm25':float(bm),'facet_ranks':{},'retrieval_channel':'keyword','review_status':'raw_original_unreviewed_as_relation'})
   item['facet_ranks'][plan['name']]=rank
 # Facets intentionally carry equal per-rank weight: a short precise source
 # paragraph should compete with an unrelated long card matching many words.
 hits=list(merged.values())
 for h in hits:h['facet_score']=sum((1.0 if k=='whole_question' else 1.5)/(20+r) for k,r in h['facet_ranks'].items())
 hits.sort(key=lambda h:(-h['facet_score'],h['chunk_id']))
 for i,h in enumerate(hits,1):h['rank']=i
 return {'hits':hits,'analysis_time':at.isoformat(),'eligible_chunks':len(rows),'eligible_corpus_fingerprint':fp,
  'statistics_scope':'eligible_as_of_corpus_only','next_cursor':None,'truncated':any(q['matched']>q['returned'] for q in queries),
  'matched_chunks':total,'queries':queries,'query_calls':len(queries),'candidate_budget_allocated':sum(quotas),
  'source_policy':'ticker soft 1.25 BM25 preference; cross-company disclosures retained'}


def diversify(fused,chunks,question,keyword):
 """Greedy per-facet coverage with a mild same-location redundancy penalty.

No original text is admitted here: these are already bounded channel candidates.
The sorted full candidate sequence makes frozen pagination deterministic.
"""
 facet_rows={h['chunk_id']:h.get('facet_ranks',{}) for h in (keyword or {}).get('hits',[])}
 remaining=list(fused);out=[];seen_facets=defaultdict(int);seen_locations=defaultdict(int)
 while remaining:
  scored=[]
  for cid,score in remaining:
   c=chunks[cid];facets=facet_rows.get(cid,{})
   novelty=sum(1/(rank*rank)/(1+seen_facets[name]) for name,rank in facets.items() if name!='whole_question')
   loc=(c['source_id'],c.get('pdf_page'),c.get('paragraph'))
   adjusted=score+0.055*novelty-0.004*min(seen_locations[loc],3)
   scored.append((adjusted,cid,score,loc))
  _,cid,score,loc=min(scored,key=lambda x:(-x[0],x[1]))
  out.append((cid,score));remaining.remove((cid,score));seen_locations[loc]+=1
  for name,rank in facet_rows.get(cid,{}).items():
   if rank==1:seen_facets[name]+=1
 return out
