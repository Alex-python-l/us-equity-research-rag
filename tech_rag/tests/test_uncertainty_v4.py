"""A cautious evidence boundary need not literally say 'may'."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,load
from integration.governance import append_claim


class BoundaryInterpretationTests(unittest.TestCase):
    def test_real_supported_platform_boundary_is_not_rejected_for_missing_may(self):
        saved=load(ROOT/'effect_v4_20261018/final_runs/AB4_MU_A/result.json')
        state=deepcopy(saved);state['claim_ledger']={};state['review_issues']=[];state['strategy_hypotheses']=[]
        wanted=None
        for output in saved['raw_role_outputs']:
            if output['tool_turn']:continue
            for claim in (output.get('parsed_payload') or {}).get('claims',[]):
                if claim.get('claim_id')=='bear_MU_nvidia_platform_relation_scope':
                    wanted=append_claim(state,claim,output['role']);break
                append_claim(state,claim,output['role'])
            if wanted:break
        self.assertIsNotNone(wanted)
        self.assertNotIn('missing_uncertainty',[i['code'] for i in wanted['issues']])
        self.assertEqual(wanted['claim']['status'],'accepted')
        self.assertTrue(wanted['claim']['semantic_review_required'])


if __name__=='__main__':unittest.main()
