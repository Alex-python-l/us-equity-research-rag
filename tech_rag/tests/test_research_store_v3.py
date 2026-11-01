"""Pure audit write plans; these tests never connect to any database."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integration.governance import new_shared_state, register_packet, append_claim, register_event


def fixture():
    at = '2026-10-17T00:00:00+00:00'
    shared = new_shared_state(at, True)
    text = 'Micron supplies memory.'
    register_packet(shared, dict(analysis_time=at, temporal_mode='strict_historical',
        citations={'C1': dict(citation_id='C1', source_id='S1', source_sha256='a'*64,
            text=text, available_at=at, historical_eligible=True, round_trip_verified=True,
            char_start=0, char_end=len(text))}, evidence_map={}, assertion_map={}, unknowns=[]))
    base = dict(claim_id='c1', text=text, type='original_fact', subject='MU', product='memory',
        business_scope='memory', evidence_ids=['C1'], parent_claim_ids=[], conditions=[],
        unknowns=[], available_at=at, valid_time={'from': None, 'to': None}, analysis_time=at,
        impact_target='business', horizon='as disclosed', status='proposed')
    append_claim(shared, base, 'researcher')
    append_claim(shared, dict(base, unknowns=['quantity unknown']), 'reviewer')
    register_event(shared, dict(original_source_id='S1', original_event_id='release1',
                               text=text, evidence_ids=['C1'], available_at=at))
    shared.update(run_id='test-run-1', tool_trace=[{'tool': 'rag', 'evidence_ids': ['C1']}],
                  raw_role_outputs={'researcher': 'Public source only.'},
                  framework_metadata={'implementation': 'official', 'model_mode': 'controlled_double'})
    return shared


class AuditPlanTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('integration.research_store'), 'audit store missing')
        from integration import research_store
        return research_store

    def test_plan_has_only_namespaced_research_nodes_and_edges(self):
        m = self.api()
        state = fixture()
        before = json.dumps(state, sort_keys=True)
        plan = m.prepare_research_plan(state)
        self.assertEqual(set(plan['nodes']), {'TechResearchRun', 'TechResearchClaimVersion',
            'TechResearchEventVersion', 'TechResearchToolInvocation'})
        self.assertEqual([len(plan['nodes'][key]) for key in ['TechResearchRun',
            'TechResearchClaimVersion', 'TechResearchEventVersion', 'TechResearchToolInvocation']], [1, 2, 1, 1])
        for group in plan['nodes'].values():
            for row in group:
                self.assertEqual(row['dataset_id'], 'tech_company_v1')
                self.assertEqual(row['audit_namespace'], 'research_v3')
                self.assertNotIn('review_status', row)
        self.assertTrue(all(kind.startswith('TECH_RESEARCH_') for kind in plan['edges']))
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        json.dumps(plan)

    def test_plan_identity_is_stable_and_changed_run_content_has_new_hash(self):
        m = self.api()
        state = fixture()
        first = m.prepare_research_plan(state)
        self.assertEqual(first, m.prepare_research_plan(state))
        state['raw_role_outputs']['researcher'] = 'New research proposal.'
        second = m.prepare_research_plan(state)
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertNotEqual(first['content_hash'], second['content_hash'])

    def test_same_audit_id_cannot_silently_overwrite_stored_payload(self):
        m = self.api()
        plan = m.prepare_research_plan(fixture())
        row = plan['nodes']['TechResearchRun'][0]
        self.assertTrue(m.assert_existing_compatible(row, dict(row)))
        altered = dict(row, content_hash='different')
        with self.assertRaises(ValueError):
            m.assert_existing_compatible(row, altered)

    def test_invalid_and_secret_bearing_payloads_are_rejected_before_connecting(self):
        m = self.api()
        for change in [dict(run_id=''), dict(tool_trace='bad'), dict(framework_metadata=[]),
                       dict(raw_role_outputs={'api_key': 'private'}),
                       dict(raw_role_outputs={'r': 'sk-abcdefghijklmnop1234567890'})]:
            state = fixture()
            state.update(change)
            with self.assertRaises(ValueError):
                m.prepare_research_plan(state)

    def test_modified_claim_version_cannot_be_persisted(self):
        m = self.api()
        state = fixture()
        state['claim_ledger']['c1'][0]['text'] = 'changed'
        with self.assertRaises(ValueError):
            m.prepare_research_plan(state)

    def test_parent_links_resolve_to_inspected_version_and_keep_version_chain(self):
        m = self.api()
        plan = m.prepare_research_plan(fixture())
        previous = plan['edges']['TECH_RESEARCH_PREVIOUS_VERSION']
        self.assertEqual(len(previous), 1)
        self.assertNotEqual(previous[0]['start_id'], previous[0]['end_id'])


if __name__ == '__main__':
    unittest.main()
