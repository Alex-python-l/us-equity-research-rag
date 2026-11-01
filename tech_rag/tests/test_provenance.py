import importlib.util,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ProvenanceTests(unittest.TestCase):
 def build(self,chunks,evidence,records=None,relations=None):
  self.assertIsNotNone(importlib.util.find_spec('provenance'),'precise provenance links not implemented')
  from provenance import build_links
  return build_links(chunks=chunks,evidence=evidence,records=records or [],relations=relations or [])
 def chunk(self,id,start,end,source='s',page=1,hash='h'):
  return {'chunk_id':id,'source_id':source,'pdf_page':page,'paragraph':None,'char_start':start,'char_end':end,'source_sha256':hash}
 def evidence(self,start=5,end=25):
  return {'evidence_id':'e','source_id':'s','pdf_page':1,'paragraph':None,'char_start':start,'char_end':end,'source_sha256':'h','quote':'x'*(end-start)}
 def test_cross_chunk_span_and_no_source_wide_false_match(self):
  r=self.build([self.chunk('a',0,10),self.chunk('b',10,30),self.chunk('same_source_other_page',0,30,page=2),self.chunk('far',100,200)],[self.evidence()])
  self.assertEqual({x['chunk_id'] for x in r['evidence_chunk_links']},{'a','b'})
  self.assertEqual([(x['overlap_start'],x['overlap_end']) for x in r['evidence_chunk_links']],[(5,10),(10,25)])
  self.assertEqual(r['locators'][0]['coverage_fraction'],1.0)
 def test_version_hash_and_non_overlapping_edges_excluded(self):
  r=self.build([self.chunk('wrong',0,30,hash='other'),self.chunk('edge',25,30)],[self.evidence()])
  self.assertFalse(r['evidence_chunk_links']);self.assertEqual(r['locators'][0]['coverage_fraction'],0)
 def test_overlap_union_not_double_counted(self):
  r=self.build([self.chunk('a',0,20),self.chunk('b',10,25)],[self.evidence()])
  self.assertEqual(r['locators'][0]['coverage_fraction'],1.0)
 def test_missing_page_and_paragraph_keeps_locator_without_fuzzy_link(self):
  e=self.evidence();e['pdf_page']=None
  c=self.chunk('a',0,30,page=None)
  r=self.build([c],[e]);self.assertEqual(len(r['locators']),1);self.assertFalse(r['evidence_chunk_links'])
 def test_relationship_card_maps_only_explicit_assertion_ids(self):
  r=self.build([],[],[{'record_id':'k','kind':'reviewed_relation','relation_ids':['r'],'evidence_ids':[]}],[{'relation_id':'r'}])
  self.assertEqual(r['record_assertion_links'],[{'record_id':'k','relation_id':'r'}])

if __name__=='__main__':unittest.main()
