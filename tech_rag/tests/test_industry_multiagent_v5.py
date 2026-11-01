import sys
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class FrontloadedMultiagentTests(unittest.TestCase):
    def test_official_roles_reuse_the_prepared_snapshot_without_rag_tools(self):
        from integration.industry_multiagent import run_multiagent
        from integration.industry_models import IndustryModels
        from integration.industry_pipeline import prepare_research
        with tempfile.TemporaryDirectory() as td:
            prepared=prepare_research('MU','HBM供给',arm='A',fetch_news=False,archive_dir=td)
            models=IndustryModels(prepared,td,mode='controlled',run_id='M5')
            with patch('research.ResearchEngine.build',side_effect=AssertionError('news后RAG')):
                out=run_multiagent(prepared,models,Path(td),'M5')
        self.assertEqual(len(out['role_trace']),9)
        self.assertEqual(out['company_rag_calls_after_news'],0)
        self.assertEqual(out['framework_metadata']['upstream_commit'],'e8f5503e9b86d5c2710b0c76b1f686703968c9df')
        self.assertFalse(out['research_gate']['execution_allowed'])
        self.assertEqual(out['run_usage']['model_requests'],0)
        self.assertFalse(out['available_model_tools'])


if __name__=='__main__':unittest.main()
