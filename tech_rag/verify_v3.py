"""Verify saved v3 executions without overwriting v2 or repeating model jobs."""
from pathlib import Path
import argparse
import hashlib
import json
import re
from common import ROOT,WORKSPACE,load,dump,now

def run(require_online=False):
    report={'verified_at':now(),'no_services_controlled':True}
    log=(ROOT/'output/v3_unit_final.log').read_text(encoding='utf-8')
    count=re.search(r'^Ran (\d+) tests',log,re.M)
    assert count and re.search(r'^OK\s*$',log,re.M),'Final integration tests not fully passing or optional checks skipped'
    report['unit_tests']={'run':int(count[1]),'passed':True,'skipped':0,
                          'environment':'integration/.venv (inherits original RAG packages)'}
    post=load(ROOT/'output/v3_postrun_revalidation.json')
    assert post['all_usable_ids_unchanged'] and post['all_ledgers_preserved'] and post['integrity_error_count']==0
    report['postrun_governance_revalidation']={k:v for k,v in post.items() if k!='runs'}
    preserved={}
    required={'chunks.jsonl','relations.json','entities.json','evidence.json','source_catalog.json',
              'embedded_records.jsonl','embeddings.npy','model_provenance.json'}
    for row in load(ROOT/'output/baseline_20261018.json'):
        p=WORKSPACE/row['path']
        if p.name in required:preserved[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
    assert len(preserved)==8 and all(preserved.values()),'Original corpus/document embeddings changed'
    report['original_data_document_vectors_preserved']=preserved
    gold={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
          [ROOT/'evaluation/questions_v2.json',ROOT/'evaluation/questions_v3_holdout.json']}
    assert gold['questions_v2.json']=='1aa12f9dc8f5f4be6c84831304c5e6a709515d67ee12fb1cf6a256bd9e5eecc2'
    assert gold['questions_v3_holdout.json']=='b546c1283b3ed4c7f7283dbf4954a77de018065f195be631c91d2988c23aafb5'
    report['frozen_question_sets']=gold
    report['retrieval']={}
    report['framework_controlled']={}
    for backend in ('local','neo4j'):
        e=load(ROOT/f'output/v3_evaluation_{backend}.json')
        assert e['all_question_budgets_equal'] and e['all_eligible_keyword_corpora_equal']
        report['retrieval'][backend]=e['summary']
        f=load(ROOT/f'output/v3_framework_evaluation_{backend}.json')
        for r in f['rows']:
            assert not r['citation_errors'] and not r['relation_errors'] and not r['historical_leaks']
            assert r['research_gate']['signal']=='no_signal' and not r['research_gate']['execution_allowed']
            assert r['run_usage']['llm_calls']<=12 and r['run_usage']['unique_evidence_chars']<=22000
        report['framework_controlled'][backend]=f['summary']
    revised=ROOT/'output/v3_online_MU_revised.json'
    online_path=revised if revised.exists() else ROOT/'output/v3_online_MU.json'
    if online_path.exists():
        online=load(online_path)
        success=any(t.get('http_status')==200 for t in online['model_trace'])
        all_ok=all(t.get('http_status')==200 and not t.get('error_type') for t in online['model_trace'])
        report['online']={'artifact':str(online_path),'calls':online['run_usage']['llm_calls'],'any_model_call_succeeded':success,
            'all_model_calls_succeeded':all_ok,'model_mode':online['framework_metadata']['model_mode'],
            'gate':online['research_gate'],'usage':online['run_usage']}
        report['online']['accepted_conditional_claims']=sum(vs[-1]['status']=='accepted' and
            vs[-1]['type']=='conditional_hypothesis' for vs in online['claim_ledger'].values() if vs)
        initial=ROOT/'output/v3_online_MU.json'
        if revised.exists() and initial.exists():
            first=load(initial)
            report['online']['initial_run_preserved']={'artifact':str(initial),
                'usage':first['run_usage'],'gate':first['research_gate']}
        if require_online:assert success and all_ok,'Online calls failed; keep failure records'
    elif require_online:raise AssertionError('No actual online artifact')
    else:report['online']={'executed':False}
    audit=load(ROOT/'output/v3_neo4j_research_audit.json')
    assert all(a['second_run_idempotent'] and not a['old_payloads_rewritten'] and
               not a['fact_review_statuses_changed'] for a in audit['runs'])
    report['neo4j_audit']=audit
    report['code_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*ROOT.glob('*.py'),*ROOT.glob('integration/*.py'),*ROOT.glob('integration/prompts/*.txt')]}
    report['boundaries']={'lean_deployed':False,'broker_connected':False,'orders_sent':False,
        'remote_quant_server_changed':False,'real_return_performance_validated':False,
        'all_chunk_embedding_submitted':False,'new_document_embeddings':0}
    dump(ROOT/'output/v3_release_verification.json',report)
    print('V3_SAVED_EXECUTION_VERIFIED')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--require-online',action='store_true');run(p.parse_args().require_online)
