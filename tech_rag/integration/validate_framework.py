"""Actual official-graph validation, kept separate from single-packet evaluation."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common import ROOT,load,dump,now
from research import ResearchEngine
from evaluate_research import anchor_hit,packet_checks,gold_relation_checks
from integration.framework import run_research


def evaluate_run(result,question,engine):
    citations={k:v for k,v in result['evidence_registry'].items() if k.startswith('C_')}
    paths=[];assertions={};truncated=False
    for p in result['evidence_packets']:
        paths.extend(p['paths']);assertions.update(p['assertion_map'])
        paths.extend(p.get('cross_tool_assembly',{}).get('paths',[]))
        assertions.update(p.get('cross_tool_assembly',{}).get('assertion_map',{}))
        truncated=truncated or p['coverage']['truncated']
    packet={'citations':citations,'paths':paths,'assertion_map':assertions,
        'analysis_time':result['analysis_time'],
        'temporal_mode':'strict_historical' if result['historical'] else 'current_as_collected'}
    anchors=[anchor_hit(engine,a,citations) for a in question['expected_evidence']]
    checked=packet_checks(engine,packet)
    rels=gold_relation_checks(question,packet,engine)
    claims=[vs[-1] for vs in result['claim_ledger'].values() if vs]
    rejected=[c for c in claims if c['status']=='rejected']
    codes=Counter(i['code'] for i in result['review_issues'])
    return {'question_id':question['id'],'ticker':question['ticker'],
        'expected_anchors':len(anchors),'full_anchor_hits':sum(a['full_hit'] for a in anchors),
        'anchors':anchors,'expected_relations':len(rels),'gold_relation_hits':sum(r['found'] for r in rels),
        'expected_status':question['expected_status'],'research_gate':result['research_gate'],
        'claims':{'accepted':sum(c['status']=='accepted' for c in claims),
                  'rejected':len(rejected),'needs_more_evidence':sum(c['status']=='needs_more_evidence' for c in claims)},
        'review_issue_codes':dict(codes),'rejected_claim_ids':[c['claim_id'] for c in rejected],
        'truncated':truncated,'tool_count':len(result['tool_trace']),
        'run_usage':result['run_usage'],'model_mode':result['framework_metadata']['model_mode'],
        'new_event_is_archived_fixture':result['received_event'].get('input_kind')=='archived_source_fixture',
        'answer_correctness':'not measured: controlled models validate plumbing and rejection, not economic inference',
        **checked}


def run(backend='local',question_ids=None):
    questions=load(ROOT/'evaluation/questions_v2.json')+load(ROOT/'evaluation/questions_v3_holdout.json')
    if question_ids:questions=[q for q in questions if q['id'] in question_ids]
    config=ROOT/'.env' if backend=='neo4j' else None
    if config:
        from neo4j_store import probe
        from neo4j_links import validate_target
        validate_target(probe(config))
    engine=ResearchEngine(backend,config);rows=[];out=ROOT/'output'/('v3_framework_'+backend)
    out.mkdir(exist_ok=True)
    for q in questions:
        result=run_research(q['ticker'],q['question'],q['as_of'],backend=backend,
            use_dense=False,bad_rewrite=q['id']=='V2_10')
        dump(out/(q['id']+'.json'),result)
        row=evaluate_run(result,q,engine);rows.append(row)
        print(json.dumps({'backend':backend,'id':q['id'],'hits':row['full_anchor_hits'],
                          'gold':row['expected_anchors'],'gate':row['research_gate']['status']}),flush=True)
    summary={}
    for name,subset in [('old24',[r for r in rows if r['question_id'].startswith('V2_')]),
                        ('holdout14',[r for r in rows if r['question_id'].startswith('V3_H')])]:
        n=sum(r['expected_anchors'] for r in subset);c=sum(r['citations_checked'] for r in subset)
        summary[name]={'questions':len(subset),'full_anchor_hits':sum(r['full_anchor_hits'] for r in subset),
            'gold_anchors':n,'full_anchor_recall':sum(r['full_anchor_hits'] for r in subset)/n if n else None,
            'citation_round_trip_rate':1-sum(len(r['citation_errors']) for r in subset)/c if c else None,
            'relation_direction_type_errors':sum(len(r['relation_errors']) for r in subset),
            'historical_leaks':sum(len(r['historical_leaks']) for r in subset),
            'all_stock_signals_disabled':all(r['research_gate'].get('signal')=='no_signal' and not r['research_gate']['execution_allowed'] for r in subset),
            'accepted_conditional_claims_exist':any(r['claims']['accepted']>1 for r in subset),
            'truncated_questions':sum(r['truncated'] for r in subset),
            'llm_calls':sum(r['run_usage']['llm_calls'] for r in subset),
            'seconds':sum(r['run_usage']['seconds'] for r in subset)}
    report={'executed_at':now(),'backend':backend,'model_mode':'controlled',
        'comparison_boundary':'Two tool opportunities per workflow; use_dense=False, aggregate original text cap 22000. This is a multi-round graph diagnostic, not the same-budget single-packet ranking benchmark.',
        'scope':'Official graph/factories + real read-only RAG, controlled model substitutes; no online LLM or trading returns claimed.',
        'summary':summary,'rows':rows}
    dump(ROOT/'output'/('v3_framework_evaluation_'+backend+'.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--backend',choices=['local','neo4j'],default='local')
    p.add_argument('--ids',nargs='*');a=p.parse_args();run(a.backend,a.ids)
