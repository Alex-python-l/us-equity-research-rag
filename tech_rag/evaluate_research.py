"""Frozen original-source annotations vs graph / reviewed-dense / unified retrieval."""
import argparse,hashlib,json,re,csv
from common import ROOT,load,readl,dump,jsonl,now,parse_time
from research import ResearchEngine,ResearchBudget
from agent import run_agent

GOLD_SHA='1aa12f9dc8f5f4be6c84831304c5e6a709515d67ee12fb1cf6a256bd9e5eecc2'

def annotated_span(engine,anchor):
 source=engine.tools.sources[anchor['source_id']]
 blocks=readl(ROOT/source['text_path'])
 block=next(b for b in blocks if b.get('pdf_page')==anchor['pdf_page'] and b.get('paragraph')==anchor['paragraph'])
 raw=block['text'];needle=anchor['needle']
 if raw[anchor['char_start']:anchor['char_end']]!=needle:raise AssertionError('gold raw anchor mismatch')
 norm=re.sub(r'\s+',' ',raw).strip();n=re.sub(r'\s+',' ',needle).strip()
 positions=[m.start() for m in re.finditer(re.escape(n),norm)]
 approximate=len(re.sub(r'\s+',' ',raw[:anchor['char_start']]).lstrip())
 if not positions:raise AssertionError('gold normalized anchor missing')
 start=min(positions,key=lambda x:abs(x-approximate))
 return start,start+len(n)

def anchor_hit(engine,anchor,citations):
 start,end=annotated_span(engine,anchor);ranges=[]
 versions=load(ROOT/'evaluation/source_versions_v2.json')
 for c in citations.values():
  if (c['source_id'],c['pdf_page'],c['paragraph'])!=(anchor['source_id'],anchor['pdf_page'],anchor['paragraph']):continue
  if c['source_sha256']!=versions[anchor['source_id']]:continue
  lo,hi=max(start,c['char_start']),min(end,c['char_end'])
  if lo<hi:ranges.append((lo,hi))
 merged=[]
 for lo,hi in sorted(ranges):
  if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
  else:merged.append((lo,hi))
 coverage=sum(b-a for a,b in merged)/(end-start)
 return {'source_id':anchor['source_id'],'pdf_page':anchor['pdf_page'],'paragraph':anchor['paragraph'],
  'full_hit':coverage==1.0,'any_overlap':coverage>0,'covered_fraction':coverage}

def gold_relation_checks(question,packet,engine):
 returned={(r['subject_id'],r['predicate'],r['object_id'],r['evidence_class']) for r in packet['assertion_map'].values()}
 catalog={(r['subject_id'],r['predicate'],r['object_id'],r['evidence_class']) for r in engine.relations.values()}
 results=[]
 for r in question['expected_relationships']:
  key=(r['subject_id'],r['predicate'],r['object_id'],r['kind'])
  results.append({**r,'found':key in returned,'present_in_curated_catalog':key in catalog})
 return results

def packet_checks(engine,p):
 bad_refs=[];leaks=[];relation_errors=[]
 at=parse_time(p['analysis_time']);historical=p['temporal_mode']=='strict_historical'
 for ref,c in p['citations'].items():
  chunk=engine.chunks_by_id[c['chunk_id']]
  offset=c['char_start']-chunk['char_start']
  if not engine.verify_chunk(chunk)[0] or chunk['text'][offset:offset+len(c['text'])]!=c['text'] or c['char_end']-c['char_start']!=len(c['text']):bad_refs.append(ref)
  if parse_time(c['available_at'])>at or (historical and not c['historical_eligible']):leaks.append(ref)
 for path in p['paths']:
  for i,s in enumerate(path['steps']):
   r=engine.relations.get(s['relation_id'])
   if not r or (s['assertion_subject'],s['assertion_predicate'],s['assertion_object'])!=(r['subject_id'],r['predicate'],r['object_id']):relation_errors.append(s['relation_id']);continue
   pair=(r['subject_id'],r['object_id']) if s['traversal_direction']=='with_assertion' else (r['object_id'],r['subject_id'])
   if (s['from_id'],s['to_id'])!=pair or s['order']!=i+1 or s['directness']!=r['directness'] or s['evidence_class']!=r['evidence_class']:relation_errors.append(s['relation_id'])
   if i and path['steps'][i-1]['to_id']!=s['from_id']:relation_errors.append(s['relation_id'])
   if not s['citation_ids'] or not set(s['citation_ids'])<=set(p['citations']):bad_refs.append(s['relation_id'])
   if not engine.tools.eligible(r,at,historical)[0]:leaks.append(r['relation_id'])
 return {'citations_checked':len(p['citations']),'citation_errors':bad_refs,'relation_errors':relation_errors,'historical_leaks':leaks}

def run(backend='local',config=None):
 path=ROOT/'evaluation/questions_v2.json'
 actual=hashlib.sha256(path.read_bytes()).hexdigest()
 if actual!=GOLD_SHA:raise RuntimeError('frozen independent question set changed; refuse silently relabeling')
 questions=load(path);engine=ResearchEngine(backend,config);rows=[];outputs=[]
 out=ROOT/'output'/f'evaluation_v2_{backend}';out.mkdir(exist_ok=True)
 for q in questions:
  for mode in ['graph','dense','unified']:
   b=ResearchBudget.from_value(q.get('budget_override'))
   p=engine.build(q['ticker'],q['question'],q['as_of'],b,mode=mode)
   anchors=[anchor_hit(engine,a,p['citations']) for a in q['expected_evidence']]
   checked=packet_checks(engine,p)
   gold_relations=gold_relation_checks(q,p,engine)
   row={'question_id':q['id'],'category':q['category'],'mode':mode,'backend':backend,
    'expected_status':q['expected_status'],'returned_status':p['status'],
    'expected_anchors':len(anchors),'full_anchor_hits':sum(x['full_hit'] for x in anchors),
    'any_overlap_hits':sum(x['any_overlap'] for x in anchors),'anchors':anchors,
    'expected_relations':len(gold_relations),'gold_relation_hits':sum(r['found'] for r in gold_relations),'gold_relationships':gold_relations,
    'forbidden_claims_evaluation':'N/A: no generated natural-language answer; source quotes must not be interpreted as endorsed answers',
    'uncurated_detail':q.get('uncurated_detail',False),'returned_results':len(p['results']),
    'returned_paths':len(p['paths']),'truncated':p['coverage']['truncated'],'stop_reason':p['stop_reason'],
    'insufficient_reported':p['status']=='insufficient','unknown_case_correctly_abstained':p['status']=='insufficient' if q['expected_status']=='insufficient' else None,
    **checked}
   rows.append(row);dump(out/f"{q['id']}_{mode}.json",p)
   print(json.dumps({k:row[k] for k in ['question_id','mode','full_anchor_hits','expected_anchors','returned_paths','returned_status']},ensure_ascii=False),flush=True)
  if q.get('requires_multi_round') or q.get('requires_state_reset') or q['id']=='V2_20':
   sid='eval_v2_'+backend+'_'+q['id']
   for turn in q.get('context_turns',[]):
    run_agent(q['ticker'],turn['question'],turn['as_of'],sid,backend,config,budget={'use_dense':True},engine=engine,refresh_news=False)
   agent=run_agent(q['ticker'],q['question'],q['as_of'],sid,backend,config,
    max_rounds=q.get('budget_override',{}).get('max_rounds',3),budget=q.get('budget_override'),engine=engine,refresh_news=False)
   dump(out/f"{q['id']}_agent.json",agent)
   p=agent['evidence_packet'];anchors=[anchor_hit(engine,a,p['citations']) for a in q['expected_evidence']]
   outputs.append({'question_id':q['id'],'rounds_used':agent['rounds_used'],'reused_valid_evidence_count':agent['reused_valid_evidence_count'],
    'stop_reason':agent['stop_reason'],'budget_usage':p['budget_usage'],'full_anchor_hits':sum(x['full_hit'] for x in anchors),'expected_anchors':len(anchors),
    'continuation_available':bool(p['pagination']['next_cursor']),**packet_checks(engine,p)})
 summaries={}
 for mode in ['graph','dense','unified']:
  rs=[r for r in rows if r['mode']==mode];n=sum(r['expected_anchors'] for r in rs);c=sum(r['citations_checked'] for r in rs)
  summaries[mode]={'questions':len(rs),'gold_anchors':n,'full_anchor_hits':sum(r['full_anchor_hits'] for r in rs),
   'full_anchor_recall':sum(r['full_anchor_hits'] for r in rs)/n,
   'any_overlap_recall':sum(r['any_overlap_hits'] for r in rs)/n,
   'citation_round_trip_rate':1-sum(len(r['citation_errors']) for r in rs)/c if c else None,
   'gold_expected_relations':sum(r['expected_relations'] for r in rs),'gold_relation_hits':sum(r['gold_relation_hits'] for r in rs),
   'gold_relation_recall':sum(r['gold_relation_hits'] for r in rs)/sum(r['expected_relations'] for r in rs),
   'citations_checked':c,'relationship_direction_or_type_errors':sum(len(r['relation_errors']) for r in rs),
   'historical_leaks':sum(len(r['historical_leaks']) for r in rs),
   'insufficient_returned':sum(r['insufficient_reported'] for r in rs),
   'known_unknown_cases':sum(r['expected_status']=='insufficient' for r in rs),
   'known_unknown_abstentions':sum(r['unknown_case_correctly_abstained'] is True for r in rs),
   'truncated_questions':sum(r['truncated'] for r in rs),
   'uncurated_full_anchor_recall':sum(r['full_anchor_hits'] for r in rs if r['uncurated_detail'])/sum(r['expected_anchors'] for r in rs if r['uncurated_detail'])}
 failures=[r for r in rows if r['full_anchor_hits']<r['expected_anchors'] or r['gold_relation_hits']<r['expected_relations'] or r['relation_errors'] or r['citation_errors'] or r['historical_leaks'] or r['unknown_case_correctly_abstained'] is False]
 report={'backend':backend,'executed_at':now(),'frozen_questions_sha256':actual,'annotation_method':'independent source-first automated curation; not human gold adjudication',
  'metric_definition':'Full anchor recall requires union of returned same-version original intervals to cover the entire independently marked normalized source span; any overlap reported separately. One evidence packet/page, fixed question budgets; agent multi-page results separately.',
  'comparability':'same packet budgets and citations verifier; graph/dense include exact supporting originals and finite nearby context, unified adds full original keyword search',
  'relationship_metric_scope':'gold_relation_recall compares independent expected triples+class; direction/type errors are internal consistency only, not external factual precision',
  'forbidden_claims_evaluation':'not_applicable_no_answer_generation; retain annotations for later LLM evaluation',
  'summary':summaries,'agent_cases':outputs,'rows':rows,'failures':failures,
  'limitations':['样本仅24题，不是完整产业链正确率。','没有执行LLM答案生成，无法测自由文本幻觉/策略收益。','关系类型错误检查对照已审核断言，不能替代原文产业专家复核。','保守available_at不等于精确首次公开时间。']}
 dump(ROOT/'output'/f'evaluation_v2_{backend}.json',report)
 jsonl(out/'comparison.jsonl',rows)
 with (out/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
  keys=['question_id','category','mode','backend','expected_status','returned_status','expected_anchors','full_anchor_hits','any_overlap_hits','returned_results','returned_paths','truncated','stop_reason']
  w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(rows)
 print(json.dumps(summaries,ensure_ascii=False,indent=2),flush=True)
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--backend',choices=['local','neo4j'],default='local');p.add_argument('--config')
 a=p.parse_args();run(a.backend,a.config)
