"""A real acquired article must reach model messages, not vanish as metadata."""
from copy import deepcopy
from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,load
from integration.industry_models import IndustryModels
from integration.context_codec import unpack_context


class CollectedBodyDeliveryTests(unittest.TestCase):
    def test_real_MU_B_compacts_only_omitted_ID_audit_without_losing_sources(self):
        prepared=load(ROOT/'industry_v5_20261019/primary_followup_AB/prepared/PRIMARY5_MU_B/prepared.json')
        with tempfile.TemporaryDirectory() as td:
            model=IndustryModels(prepared,td,mode='controlled',run_id='mu-full')
            expected=deepcopy(model.delivered_news)
            model.invoke('research')
            self.assertLessEqual(model.traces[-1]['input_chars'],24000)
            request=load(Path(td)/'01_research_input.json')['request']
            view=unpack_context(json.loads(request['messages'][1]['content'].split('\n',1)[1]))
            self.assertEqual(set(view['news_evidence']),set(expected))
            for cid in expected:self.assertEqual(view['news_evidence'][cid]['text'],expected[cid]['text'])
            self.assertTrue(view['background']['assertion_map'])

    def test_real_body_whole_paragraphs_and_scope_enter_actual_request_for_both_arms(self):
        article=load(ROOT/'industry_v5_20261019/primary_gap_followup/NVDA_article.json')
        original={p['citation_id']:p for p in article['paragraphs']}
        for arm in ('A','B'):
            with self.subTest(arm=arm),tempfile.TemporaryDirectory() as td:
                prepared=load(ROOT/f'industry_v5_20261019/primary_prepared/IND5_NVDA_{arm}/prepared.json')
                prepared['evidence_map']={cid:{**row,**{k:article.get(k) for k in (
                    'title','url','publisher','published_at','first_retrieved_at','body_sha256','raw_sha256',
                    'version_id','raw_path','version_scope','historical_eligible','as_of_eligible','modified_at')}} for cid,row in original.items()}
                model=IndustryModels(prepared,td,mode='controlled',run_id='delivery')
                self.assertTrue(model.delivered_news,'Valid real article disappeared before model context')
                model.invoke('research')
                request=load(Path(td)/'01_research_input.json')['request']
                view=unpack_context(json.loads(request['messages'][1]['content'].split('\n',1)[1]))
                self.assertTrue(view['news_source_versions'])
                for cid,item in view['news_evidence'].items():
                    self.assertEqual(item['text'],original[cid]['text'])
                    self.assertEqual(item.get('heading_path'),original[cid].get('heading_path'))
                self.assertLessEqual(sum(len(m['content']) for m in request['messages']),24000)


if __name__=='__main__':unittest.main()
