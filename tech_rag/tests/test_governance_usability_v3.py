"""A stored accepted version is unusable after its dependency changes."""
import copy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from integration import governance as api
from test_governance_v3 import AT, packet, claim


class CurrentClaimUsabilityTests(unittest.TestCase):
    def state(self):
        shared = api.new_shared_state(AT, True)
        api.register_packet(shared, packet())
        api.append_claim(shared, claim(conditions=['qualification required']), 'news')
        api.append_claim(shared, claim(claim_id='child', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', conditions=['qualification required'],
            parent_claim_ids=['c1']), 'bull')
        return shared

    def test_stale_parent_makes_descendant_unusable_without_rewriting_audit(self):
        shared = self.state()
        prior = copy.deepcopy(shared['claim_ledger']['child'][0])
        api.append_claim(shared, claim(conditions=['qualification required', 'capacity constrained']), 'news')
        self.assertEqual(shared['claim_ledger']['child'][0], prior)
        self.assertEqual(shared['claim_ledger']['child'][0]['status'], 'accepted')
        self.assertTrue(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))
        self.assertEqual([c['claim_id'] for c in api.eligible_claims(shared)], ['c1'])

    def test_append_refuses_a_stale_child_as_parent(self):
        shared = self.state()
        api.append_claim(shared, claim(conditions=['qualification required', 'capacity constrained']), 'news')
        result = api.append_claim(shared, claim(claim_id='grandchild', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', conditions=['qualification required'],
            parent_claim_ids=['child']), 'trader')
        self.assertEqual(result['claim']['status'], 'rejected')
        self.assertIn('unqualified_parent', [i['code'] for i in result['issues']])

    def test_new_evidence_unknowns_disable_a_claim_and_all_descendants(self):
        shared = self.state()
        revised = packet()
        revised['unknowns'].append('inventory buffers unknown')
        api.register_packet(shared, revised)
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))
        self.assertEqual(api.eligible_claims(shared), [])
        result = api.append_claim(shared, claim(claim_id='grandchild', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', conditions=['qualification required'],
            unknowns=['quantity undisclosed', 'inventory buffers unknown'],
            parent_claim_ids=['child']), 'trader')
        self.assertEqual(result['claim']['status'], 'rejected')

    def test_explicit_new_versions_restore_current_dependency_chain(self):
        shared = self.state()
        conditions = ['qualification required', 'capacity constrained']
        api.append_claim(shared, claim(conditions=conditions), 'news')
        out = api.append_claim(shared, claim(claim_id='child', type='conditional_hypothesis',
            text='If qualified, deliveries may improve.', conditions=conditions,
            parent_claim_ids=['c1']), 'bull')
        self.assertEqual(out['claim']['status'], 'accepted')
        self.assertTrue(api.claim_usable(shared, 'child'))
        result = api.eligible_claims(shared)
        self.assertEqual([c['claim_id'] for c in result], ['c1', 'child'])
        result[-1]['conditions'].clear()
        self.assertEqual(shared['claim_ledger']['child'][-1]['conditions'], conditions)

    def test_rejected_latest_version_cannot_fall_back_to_an_old_acceptance(self):
        shared = self.state()
        api.append_claim(shared, claim(text='Unsupported statement.'), 'news')
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))
        self.assertEqual(api.eligible_claims(shared), [])

    def test_modified_ancestor_cannot_support_current_descendant(self):
        shared = self.state()
        shared['claim_ledger']['c1'][0]['text'] = 'Tampered record'
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))
        self.assertFalse(api.claim_usable(shared, 'does_not_exist'))

    def test_self_referential_parent_metadata_fails_closed(self):
        shared = self.state()
        # Re-seal a deliberately malformed persisted record to exercise cycle
        # detection rather than only the accidental-edit hash boundary.
        current = copy.deepcopy(shared['claim_ledger']['c1'][0])
        current.pop('record_hash')
        current['parent_versions'] = {'c1': 1}
        current['parent_claim_ids'] = ['c1']
        shared['claim_ledger']['c1'][0] = api._seal(current)
        self.assertFalse(api.claim_usable(shared, 'c1'))
        self.assertFalse(api.claim_usable(shared, 'child'))


class ExactSentenceFactTests(unittest.TestCase):
    def check_quote(self, source, text, **changes):
        shared = api.new_shared_state(AT, True)
        api.register_packet(shared, packet(source))
        return api.append_claim(shared, claim(text=text, **changes), 'news')

    def test_fact_cannot_remove_a_negated_sentence_prefix(self):
        out = self.check_quote('It is not true that Micron supplies general memory to NVIDIA.',
                               'Micron supplies general memory to NVIDIA.')
        self.assertEqual(out['claim']['status'], 'rejected')
        self.assertIn('incomplete_original_sentence', [i['code'] for i in out['issues']])

    def test_fact_cannot_remove_a_nonconfirmation_prefix(self):
        out = self.check_quote('We cannot confirm that Micron supplies general memory to NVIDIA.',
                               'Micron supplies general memory to NVIDIA.')
        self.assertEqual(out['claim']['status'], 'rejected')

    def test_partial_sentence_is_refused_even_if_condition_is_copied(self):
        full = 'Micron supplies general memory to NVIDIA subject to qualification.'
        out = self.check_quote(full, 'Micron supplies general memory to NVIDIA', conditions=[full])
        self.assertEqual(out['claim']['status'], 'rejected')

    def test_complete_conditional_original_sentence_is_accepted(self):
        full = 'Micron supplies general memory to NVIDIA if qualification requirements are met.'
        out = self.check_quote('The product is still being qualified. '+full, full, conditions=[full])
        self.assertEqual(out['claim']['status'], 'accepted')

    def test_contiguous_complete_original_sentences_are_accepted(self):
        full = 'Micron supplies general memory to NVIDIA. Delivery volumes remain undisclosed.'
        out = self.check_quote('The report covers supply constraints. '+full+' Costs may change.', full)
        self.assertEqual(out['claim']['status'], 'accepted')

    def test_complete_original_container_quote_is_accepted(self):
        full = 'Micron supplies general memory to NVIDIA; qualification and volume remain uncertain'
        out = self.check_quote(full, full)
        self.assertEqual(out['claim']['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
