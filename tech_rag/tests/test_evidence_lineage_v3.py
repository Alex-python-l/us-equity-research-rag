"""Derived EV quotes must retain every constraint of their original C coverage."""
import copy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from integration import governance as api
from test_governance_v3 import AT, packet, claim


class EvidenceLineageTests(unittest.TestCase):
    def state(self):
        shared = api.new_shared_state(AT, True)
        original = packet()
        api.register_packet(shared, original)
        return shared, original

    def derived(self, citation, refs=None):
        evidence = {k: citation[k] for k in
            ('source_id', 'source_sha256', 'pdf_page', 'paragraph', 'char_start', 'char_end')}
        evidence.update(evidence_id='EVX', citation_ids=refs or ['C1'], fully_covered=True,
                        quote_round_trip_verified=True)
        return dict(analysis_time=AT, temporal_mode='strict_historical', citations={},
                    evidence_map={'EVX': evidence}, assertion_map={}, unknowns=[])

    def test_cross_tool_derived_quote_inherits_earlier_original_unknowns(self):
        shared, p = self.state()
        result = api.register_packet(shared, self.derived(p['citations']['C1']))
        self.assertFalse(result['issues'])
        self.assertEqual(shared['evidence_observations']['EVX'][0]['packet_unknowns'], ['quantity undisclosed'])
        out = api.append_claim(shared, claim(evidence_ids=['EVX'], unknowns=[]), 'news')
        self.assertEqual(out['claim']['status'], 'rejected')
        self.assertIn('dropped_evidence_unknowns', [i['code'] for i in out['issues']])

    def test_complete_derived_quote_and_inherited_limits_pass(self):
        shared, p = self.state()
        api.register_packet(shared, self.derived(p['citations']['C1']))
        out = api.append_claim(shared, claim(evidence_ids=['EVX'], conditions=['qualification required']), 'news')
        self.assertEqual(out['claim']['status'], 'accepted')
        child = api.append_claim(shared, claim(claim_id='child', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', evidence_ids=['EVX'],
            conditions=['qualification required'], parent_claim_ids=['c1']), 'bull')
        self.assertEqual(child['claim']['status'], 'accepted')
        self.assertTrue(api.claim_usable(shared, 'child'))

    def test_later_original_unknowns_reach_existing_derived_claim_and_descendants(self):
        shared, p = self.state()
        api.register_packet(shared, self.derived(p['citations']['C1']))
        api.append_claim(shared, claim(evidence_ids=['EVX']), 'news')
        api.append_claim(shared, claim(claim_id='child', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', conditions=['if qualified'],
            evidence_ids=['EVX'], parent_claim_ids=['c1']), 'bull')
        before = copy.deepcopy(shared['claim_ledger'])
        p['unknowns'].append('qualification unconfirmed')
        api.register_packet(shared, p)
        self.assertEqual(shared['claim_ledger'], before)
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))
        report = api.review_ledger(shared)
        self.assertIn('new_evidence_unknowns_require_review', [i['code'] for i in report['issues']])
        self.assertEqual(report['research_gate']['status'], 'needs_more_evidence')
        rejected = api.append_claim(shared, claim(claim_id='bypass', evidence_ids=['EVX']), 'news')
        self.assertEqual(rejected['claim']['status'], 'rejected')
        accepted = api.append_claim(shared, claim(claim_id='revalidated', evidence_ids=['EVX'],
            unknowns=p['unknowns']), 'reviewer')
        self.assertEqual(accepted['claim']['status'], 'accepted')
        self.assertTrue(api.claim_usable(shared, 'revalidated'))

    def test_two_original_chunks_contribute_union_of_unknowns(self):
        shared = api.new_shared_state(AT, True)
        p = packet()
        original = p['citations']['C1']
        text = original['text']
        p['citations']['C1'] = dict(original, text=text[:20], char_end=20)
        api.register_packet(shared, p)
        p2 = copy.deepcopy(p)
        p2['citations'] = {'C2': dict(original, citation_id='C2', text=text[20:], char_start=20)}
        p2['unknowns'] = ['qualification unconfirmed']
        api.register_packet(shared, p2)
        result = api.register_packet(shared, self.derived(original, ['C1', 'C2']))
        self.assertFalse(result['issues'])
        self.assertEqual(set(shared['evidence_observations']['EVX'][0]['packet_unknowns']),
                         {'quantity undisclosed', 'qualification unconfirmed'})
        out = api.append_claim(shared, claim(evidence_ids=['EVX'],
            unknowns=['quantity undisclosed', 'qualification unconfirmed']), 'news')
        self.assertEqual(out['claim']['status'], 'accepted')

    def test_derived_quote_cannot_use_another_ev_as_original_chunk(self):
        shared, p = self.state()
        d = self.derived(p['citations']['C1'])
        api.register_packet(shared, d)
        nested = copy.deepcopy(d)
        nested['evidence_map'] = {'EVY': dict(d['evidence_map']['EVX'], evidence_id='EVY', citation_ids=['EVX'])}
        result = api.register_packet(shared, nested)
        self.assertTrue(result['issues'])
        self.assertNotIn('EVY', shared['evidence_registry'])

    def test_changed_original_version_invalidates_derived_lineage(self):
        shared, p = self.state()
        api.register_packet(shared, self.derived(p['citations']['C1']))
        api.append_claim(shared, claim(evidence_ids=['EVX']), 'news')
        modified = copy.deepcopy(shared['evidence_registry']['C1'])
        modified.pop('record_hash')
        modified['source_sha256'] = 'b'*64
        shared['evidence_registry']['C1'] = api._seal(modified)
        self.assertFalse(api.claim_usable(shared, 'c1'))
        out = api.append_claim(shared, claim(claim_id='new', evidence_ids=['EVX']), 'news')
        self.assertEqual(out['claim']['status'], 'rejected')

    def test_self_referential_derived_observation_fails_closed(self):
        shared, p = self.state()
        api.register_packet(shared, self.derived(p['citations']['C1']))
        api.append_claim(shared, claim(evidence_ids=['EVX']), 'news')
        modified = copy.deepcopy(shared['evidence_observations']['EVX'][0])
        modified.pop('record_hash')
        modified['original_record']['citation_ids'] = ['EVX']
        shared['evidence_observations']['EVX'][0] = api._seal(modified)
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertEqual(api.review_ledger(shared)['research_gate']['status'], 'blocked')

    def test_incomplete_chunk_coverage_cannot_be_promoted(self):
        shared, p = self.state()
        d = self.derived(p['citations']['C1'])
        d['evidence_map']['EVX']['char_end'] += 1
        result = api.register_packet(shared, d)
        self.assertTrue(result['issues'])
        self.assertNotIn('EVX', shared['evidence_registry'])


if __name__ == '__main__':
    unittest.main()
