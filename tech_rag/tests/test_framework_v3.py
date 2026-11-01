import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PromptTests(unittest.TestCase):
    def test_all_role_prompts_are_actual_files_with_common_contract(self):
        from integration.prompts import ROLES, load_prompt
        self.assertEqual(len(ROLES), 9)
        for role in ROLES:
            p = load_prompt(role)
            self.assertIn('parent_claim_ids', p['text'])
            self.assertIn('research_read_only', p['text'])
            self.assertEqual(len(p['sha256']), 64)

    def test_received_event_and_retrieval_share_the_same_citation_identity(self):
        from integration.runtime import EvidenceRuntime
        from integration.governance import new_shared_state, register_packet
        from research import ResearchEngine
        from datetime import datetime, timezone
        at=datetime.now(timezone.utc).isoformat()
        rt=EvidenceRuntime('MU','HBM4',at,False,use_dense=False)
        c=next(c for c in rt.engine.chunks if c['source_id']=='MU_HBM4_2026' and c.get('paragraph')==11)
        citation=rt._citation(c)
        state=new_shared_state(at,False)
        register_packet(state,{'analysis_time':at,'temporal_mode':'current_as_collected',
            'citations':{citation['citation_id']:citation},'unknowns':[]})
        engine=ResearchEngine(chunks=[c],index_path=':memory:',tools=rt.engine.tools)
        packet=engine.build('MU','HBM4','current',{'use_dense':False,'max_excerpt_chars':2200,'context_radius':0},
                            temporal_context={'analysis_time':at,'historical':False})
        result=register_packet(state,packet)
        self.assertIn(citation['citation_id'],packet['citations'])
        self.assertFalse(any(i['code']=='registry_id_collision' for i in result['issues']))

    def test_partial_quote_is_completed_across_tool_rounds(self):
        from integration.runtime import EvidenceRuntime
        from integration.governance import new_shared_state,register_packet
        from datetime import datetime,timezone
        at=datetime.now(timezone.utc).isoformat()
        rt=EvidenceRuntime('MU','HBM',at,False,use_dense=False)
        pairs=[]
        for eid,ids in rt.engine.e2c.items():
            chunks=sorted([rt.engine.chunks_by_id[i] for i in ids],key=lambda c:c['char_start'])
            if len(chunks)>1 and rt.engine.tools.evidence[eid]['char_end']>chunks[0]['char_end']:
                pairs.append((eid,chunks))
        self.assertTrue(pairs)
        eid,chunks=pairs[0];state=new_shared_state(at,False)
        def register(values):
            cs={rt._citation(c)['citation_id']:rt._citation(c) for c in values}
            register_packet(state,{'analysis_time':at,'temporal_mode':'current_as_collected',
                                  'citations':cs,'unknowns':[]})
            return rt.reconcile(state)
        first=register(chunks[:1]);self.assertFalse(first['evidence_map'][eid]['fully_covered'])
        self.assertNotIn(eid,state['evidence_registry'])
        second=register(chunks[1:]);self.assertTrue(second['evidence_map'][eid]['fully_covered'])
        self.assertIn(eid,state['evidence_registry'])


@unittest.skipUnless(importlib.util.find_spec('langgraph'), 'use integration/.venv for real framework tests')
class FrameworkTests(unittest.TestCase):
    def test_real_graph_calls_rag_and_passes_conditioned_claims(self):
        from integration.framework import run_archived_research_v3 as run_research
        r = run_research('MU', 'HBM 供应受限的影响需要什么条件？',
                         'current', use_dense=False, bad_rewrite=True)
        self.assertEqual(r['framework_metadata']['upstream_commit'],
                         'e8f5503e9b86d5c2710b0c76b1f686703968c9df')
        self.assertTrue(any(t['tool'] == 'company_research' for t in r['tool_trace']))
        self.assertTrue(any(t['tool'] == 'source_followup' for t in r['tool_trace']))
        self.assertEqual({t['role'] for t in r['role_trace']},
                         {'news','bull','bear','research_manager','trader','aggressive',
                          'conservative','neutral','portfolio_manager'})
        claims = [v for versions in r['claim_ledger'].values() for v in versions]
        self.assertTrue(any(c['status'] == 'accepted' and c['type'] == 'conditional_hypothesis' for c in claims))
        self.assertTrue(any(c['status'] == 'rejected' for c in claims))
        self.assertFalse(r['research_gate']['execution_allowed'])
        self.assertEqual(r['research_gate']['signal'], 'no_signal')
        self.assertTrue(r['evidence_registry'])
        self.assertFalse(any(i['code']=='registry_id_collision' for i in r['review_issues']))
        self.assertLessEqual(r['run_usage']['llm_calls'], 12)
        self.assertLessEqual(r['run_usage']['unique_evidence_chars'], 22000)
        json.dumps(r, allow_nan=False)

    def test_historical_tools_fail_closed_and_clock_is_shared(self):
        from integration.framework import run_archived_research_v3 as run_research
        r = run_research('NVDA', '2024年具名内存供应商，不能推定HBM型号',
                         '2024-06-01T12:00:00+08:00', use_dense=False)
        self.assertTrue(r['historical'])
        for c in r['evidence_registry'].values():
            from datetime import datetime
            self.assertLessEqual(datetime.fromisoformat(c['available_at']), datetime.fromisoformat(r['analysis_time']))
            self.assertTrue(c['historical_eligible'])
        self.assertFalse(r['tool_permissions']['macro']['enabled'])
        self.assertFalse(r['tool_permissions']['live_market']['enabled'])

    def test_external_news_metadata_does_not_become_an_event(self):
        from integration.framework import run_archived_research_v3 as run_research
        r = run_research('TSM','先进封装产能变化是否必然增加利润？','current',
                         use_dense=False,
                         event={'headline':'unverified capacity claim','url':'https://example.invalid/'})
        self.assertFalse(r['received_event']['body_acquired'])
        self.assertFalse(r['received_event']['source_verified'])
        self.assertEqual(r['event_registry'],{})
        self.assertIn('candidate',r['received_event']['input_kind'])
        self.assertFalse(r['research_gate']['execution_allowed'])


if __name__ == '__main__':
    unittest.main()
