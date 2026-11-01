import sys,unittest,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ContextSelectionTests(unittest.TestCase):
    def test_late_product_evidence_is_visible_within_a_small_context(self):
        from integration.context import select_evidence
        registry={f'C_{i}':{'text':'Micron HBM4 36GB 12H marketing navigation','registry_kind':'exact_original_quote'} for i in range(20)}
        registry['C_late']={'text':'Micron has shipped HBM4 48GB 16H samples to customers.','registry_kind':'exact_original_quote'}
        selected=select_evidence(registry,'HBM4 36GB 12H and 48GB 16H sampling',limit=3)
        self.assertIn('C_late',selected)
        self.assertLessEqual(len(selected),3)

    def test_required_parent_evidence_is_kept_whole(self):
        from integration.context import select_evidence
        registry={'EV_parent':{'text':'Qualified parent original quote.','registry_kind':'reviewed_exact_evidence'},
                  'C_1':{'text':'HBM4 48GB samples.','registry_kind':'exact_original_quote'}}
        selected=select_evidence(registry,'HBM4 48GB',required=['EV_parent'],limit=1)
        self.assertEqual(list(selected),['EV_parent'])
        self.assertEqual(selected['EV_parent']['text'],registry['EV_parent']['text'])

    def test_required_evidence_is_never_silently_cut_by_optional_limit(self):
        from integration.context import select_evidence
        registry={f'EV_{i}':{'text':'Complete required quotation '+str(i)} for i in range(7)}
        selected=select_evidence(registry,'HBM',required=list(registry),limit=6)
        self.assertEqual(set(selected),set(registry))

    @unittest.skipUnless(importlib.util.find_spec('langchain_core'),'integration interpreter required')
    def test_oversized_mandatory_evidence_stops_before_provider_call(self):
        from integration.models import RoleModel
        from types import SimpleNamespace
        from unittest.mock import patch
        evidence={f'EV_{i}':{'text':'x'*3000} for i in range(7)}
        rt=SimpleNamespace(mode='online',online_failed=False,llm_calls=0,max_llm_calls=12,
            analysis_time='2026-10-18T00:00:00+00:00',historical=False,company={'symbol':'MU'},
            question='HBM4',state={'evidence_registry':evidence,'claim_ledger':{}},
            source_ids=set(),models={},model_trace=[],tools=(),llm_config=None)
        model=RoleModel(rt,'news');rt.models['news']=model
        with patch('integration.models.claim_template',return_value={'evidence_ids':list(evidence)}):
            model.invoke('untrusted legacy text')
        self.assertEqual(rt.model_trace[-1]['status'],'required_context_budget')
        self.assertFalse(rt.model_trace[-1].get('http_request_attempted',False))
        self.assertFalse(rt.online_failed)

if __name__=='__main__':unittest.main()
