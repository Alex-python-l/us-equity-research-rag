import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag_package import RagPackages,freeze

class PackageTests(unittest.TestCase):
    def test_entire_payload_is_trusted_not_self_reported_hash(self):
        with tempfile.TemporaryDirectory() as td:
            original=freeze(dict(ticker='NVDA',paths=[dict(condition='only if qualified')],unknowns=['share']),td)
            self.assertEqual(freeze(original,td),original)
            changed=copy.deepcopy(original);changed['paths'][0]['condition']='always'
            with self.assertRaises(ValueError):RagPackages(td).verify(changed,changed['rag'])
            changed['rag']['sha256']='0'*64
            with self.assertRaises(ValueError):RagPackages(td).get(changed['rag'])
    def test_snapshot_does_not_change_with_downstream_results(self):
        with tempfile.TemporaryDirectory() as td:
            package=freeze(dict(ticker='MU',facts=['background']),td)
            run=dict(rag=package['rag'],event_analysis_versions=[dict(status='unknown')])
            run['event_analysis_versions'].append(dict(status='observed'))
            self.assertEqual(RagPackages(td).get(run['rag'])['facts'],['background'])
