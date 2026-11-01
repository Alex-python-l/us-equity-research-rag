"""Free prose in a gap field must not become an approved factual channel."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from integration.industry_pipeline import audit_findings


class GapChannelTests(unittest.TestCase):
    def test_unsupported_fact_in_unknowns_is_kept_as_unverified_note_not_safe_fact(self):
        text='Company has guaranteed $100 billion new HBM4 orders.'
        got=audit_findings({'findings':[],'unknowns':[text]}, {'reviews':[],'unknowns':[text]}, {}, None)
        self.assertNotIn(text,got['unknowns'])
        self.assertEqual(got['model_gap_notes_unverified'][0]['text'],text)
        self.assertFalse(got['model_gap_notes_unverified'][0]['fact_promotion_allowed'])
        self.assertEqual(got['answer_status'],'insufficient')

    def test_known_host_gaps_and_valid_finding_unknowns_survive(self):
        got=audit_findings({'findings':[],'unknowns':['Specific allocation is unknown.']},
                           {'reviews':[]}, {}, {'unknowns':['Dataset covers only three companies.']})
        self.assertIn('Dataset covers only three companies.',got['unknowns'])
        self.assertTrue(got['model_gap_notes_unverified'])


if __name__=='__main__':unittest.main()
