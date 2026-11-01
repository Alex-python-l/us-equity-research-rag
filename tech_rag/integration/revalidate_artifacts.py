"""Recheck saved runs under final guards without regenerating or rewriting claims."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,load,dump,now
from integration.governance import review_ledger,eligible_claims

def run():
    paths=[*sorted((ROOT/'output/v3_framework_local').glob('*.json')),
           *sorted((ROOT/'output/v3_framework_neo4j').glob('*.json')),
           ROOT/'output/v3_online_MU.json',ROOT/'output/v3_online_MU_revised.json']
    rows=[]
    for p in paths:
        result=load(p);before=json.dumps(result['claim_ledger'],sort_keys=True,ensure_ascii=False)
        old_issues=list(result['review_issues'])
        saved={c['claim_id'] for c in result['research_gate'].get('currently_usable_claim_versions',[])}
        checked=review_ledger(result);usable={c['claim_id'] for c in eligible_claims(result)}
        assert before==json.dumps(result['claim_ledger'],sort_keys=True,ensure_ascii=False)
        rows.append({'artifact':str(p.relative_to(ROOT)),'saved_usable_ids':sorted(saved),
            'revalidated_usable_ids':sorted(usable),'usable_ids_unchanged':saved==usable,
            'gate_status':checked['research_gate']['status'],
            'additional_review_issues':[i for i in checked['issues'] if i not in old_issues],
            'integrity_errors':[i for i in checked['issues'] if i['code'] in
                ('registry_integrity_violation','ledger_integrity_violation')],
            'original_ledger_unchanged':True})
    report={'executed_at':now(),'validation':'Final transitive original-C unknown lineage and current-parent checks; no LLM generation or database writes',
        'runs':rows,'all_usable_ids_unchanged':all(r['usable_ids_unchanged'] for r in rows),
        'integrity_error_count':sum(len(r['integrity_errors']) for r in rows),
        'all_ledgers_preserved':True}
    dump(ROOT/'output/v3_postrun_revalidation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='runs'},ensure_ascii=False))
    return report

if __name__=='__main__':run()
