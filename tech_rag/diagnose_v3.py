"""Read-only V2 failure attribution, using cached query vectors (never a model)."""
import json, sqlite3, sys, os, subprocess
from collections import Counter
from contextlib import closing
from unittest.mock import patch
from common import ROOT, load, dump, now


def cached_query_vector(query, signature):
 import numpy as np
 with closing(sqlite3.connect(f'file:{ROOT / "state/tech_query_vectors.sqlite3"}?mode=ro',uri=True)) as db:
  row=db.execute('SELECT vector FROM vectors WHERE query=? AND signature=?',(query,signature)).fetchone()
 if row is None:raise RuntimeError('No frozen query vector; refusing to initialize embedding model')
 return np.frombuffer(row[0],dtype=np.float32).copy(),True


def diagnose():
 if '--baseline-worker' not in sys.argv:
  result=subprocess.run([sys.executable,str(ROOT/'diagnose_v3.py'),'--baseline-worker'],capture_output=True,
   text=True,encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'},timeout=120)
  if result.returncode:raise RuntimeError(result.stderr[-3000:])
  return load(ROOT/'output/v3_failure_diagnosis.json')
 sys.path.insert(0,str(ROOT/'state/baseline_v3_20261018'))
 from lexical import freeze
 from research import ResearchEngine,ResearchBudget
 from evaluate_research import annotated_span,anchor_hit
 engine=ResearchEngine(index_path=ROOT/'state/v3_diagnose_lexical.sqlite3');rows=[]
 with patch('dense.query_vector',side_effect=cached_query_vector):
  for q in load(ROOT/'evaluation/questions_v2.json'):
   old=load(ROOT/'output/evaluation_v2_local'/f"{q['id']}_unified.json")
   at,historical=freeze(q['as_of']);budget=ResearchBudget.from_value(q.get('budget_override'))
   kw=engine.index.search(q['question'],q['as_of'],top_k=budget.keyword_candidates,at=at,historical=historical)
   dense=engine.dense_search(q['question'],at,historical,budget.dense_candidates)
   dense_ids={cid for hit in dense['hits'] for eid in hit['evidence_ids'] for cid in engine.e2c[eid]}
   # Graph endpoints may have been seeded by lexical/dense channels. Preserve
   # those exact allowed endpoints, rather than using a future entity catalog.
   seeds=[]
   for hit in dense['hits'][:6]:
    for rid in hit['relation_ids']:
     r=engine.relations.get(rid)
     if r and engine.tools.eligible(r,at,historical)[0]:seeds.extend([r['subject_id'],r['object_id']])
   for hit in kw['hits'][:6]:
    for eid in engine.c2e[hit['chunk_id']]:
     for rid in engine.e2r[eid]:
      r=engine.relations[rid]
      if engine.tools.eligible(r,at,historical)[0]:seeds.extend([r['subject_id'],r['object_id']])
   graph=engine.graph_search(q['ticker'],q['question'],at,historical,budget,seeds)
   graph_ids={cid for p in graph['paths'] for s in p['steps'] for eid in s['evidence_ids'] for cid in engine.e2c[eid]}
   kw_ranks={x['chunk_id']:x['rank'] for x in kw['hits']}
   for ai,a in enumerate(q['expected_evidence']):
    measured=anchor_hit(engine,a,old['citations'])
    if measured['full_hit']:continue
    start,end=annotated_span(engine,a)
    original=[c for c in engine.chunks if (c['source_id'],c.get('pdf_page'),c.get('paragraph'))==(a['source_id'],a['pdf_page'],a['paragraph']) and c['char_start']<end and c['char_end']>start]
    ids={c['chunk_id'] for c in original};candidate_ids=ids & (set(kw_ranks)|dense_ids|graph_ids)
    selected={x['chunk_id'] for x in old['results']};visible={c['chunk_id'] for c in old['citations'].values()}
    if not original:category='未收集'
    elif not candidate_ids:category='未入候选'
    elif measured['any_overlap'] or ids & (selected|visible) or budget.max_total_chars<1000:category='部分页或预算'
    else:category='排序落后'
    rows.append({'question_id':q['id'],'anchor_index':ai,'category':category,'anchor':a,
     'normalized_span':[start,end],'original_chunk_ids':sorted(ids),'keyword_candidate_ranks':{cid:kw_ranks[cid] for cid in sorted(ids & set(kw_ranks))},
     'dense_candidate_chunk_ids':sorted(ids & dense_ids),'graph_candidate_chunk_ids':sorted(ids & graph_ids),
     'old_selected_chunk_ids':sorted(ids & selected),'old_coverage':measured,'old_stop_reason':old['stop_reason'],
     'budget':old['budget'],'interpretation':'candidate presence reconstructed from frozen eligible channels; no embedding initialization'})
   for rel in q['expected_relationships']:
    matching=[r for r in engine.relations.values() if (r['subject_id'],r['predicate'],r['object_id'],r['evidence_class'])==(rel['subject_id'],rel['predicate'],rel['object_id'],rel['kind'])]
    if not any(r['relation_id'] in old['assertion_map'] for r in matching):
     rows.append({'question_id':q['id'],'category':'断言组装','expected_relationship':rel,
      'matching_catalog_relation_ids':[r['relation_id'] for r in matching],
      'evidence_visibility':{r['relation_id']:{eid:old['evidence_map'].get(eid) for eid in r['evidence_ids']} for r in matching},
      'interpretation':'assertion requires all reviewed supporting intervals; missing relation does not imply absent source fact'})
 counts=Counter(r['category'] for r in rows)
 result={'executed_at':now(),'baseline':'frozen V2 original packets + pre-change eligible keyword/graph and exact cached dense vectors',
  'question_set':'questions_v2.json only; holdout unread','embedding_model_initialized':False,
  'categories':{k:counts[k] for k in ['未收集','未入候选','排序落后','部分页或预算','断言组装']},'failures':rows,
  'decision':'Failures span missing translations/morphology, candidate ranking, long-source coverage and excerpt budgets; no default full-chunk embedding is justified.'}
 dump(ROOT/'output/v3_failure_diagnosis.json',result)
 return result


if __name__=='__main__':
 result=diagnose();print(json.dumps(result['categories'],ensure_ascii=False))
