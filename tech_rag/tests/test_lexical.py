import importlib.util,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def row(id,text,at='2024-01-01T00:00:00+00:00',historical=True):
 return {'chunk_id':id,'source_id':'s'+id,'text':text,'available_at':at,'historical_eligible':historical,'ticker':'MU',
 'source_sha256':'hash'+id,'pdf_page':1,'paragraph':None,'char_start':0,'char_end':len(text)}

class LexicalTests(unittest.TestCase):
 def module(self,name):
  self.assertIsNotNone(importlib.util.find_spec(name),'missing research module '+name)
  return __import__(name)
 def test_deterministic_company_alias_and_security_distinction(self):
  m=self.module('aliases')
  a=m.resolve_company('美光');b=m.resolve_company('MU')
  self.assertEqual(a['company']['entity_id'],'MU');self.assertIsNone(a['selected_security'])
  self.assertEqual(b['selected_security']['symbol'],'MU')
  self.assertEqual(m.resolve_company('台积电')['research_symbol'],'TSM')
  self.assertEqual(m.resolve_company('TSM')['selected_security']['instrument_type'],'ADS')
  with self.assertRaises(ValueError):m.resolve_company('2330')
  with self.assertRaises(ValueError):m.resolve_company('not a company')
 def test_chinese_alias_product_model_and_numeric_query(self):
  m=self.module('lexical');rows=[row('a','Micron 9650 SSD sequential read bandwidth 28 GB/s 5.5 million IOPS'),row('b','NVIDIA H200 HBM3E memory'),row('c','TSMC CoWoS-S silicon interposer')]
  with tempfile.TemporaryDirectory() as d:
   idx=m.KeywordIndex(Path(d)/'tech.sqlite3',chunks=rows)
   r=idx.search('美光 9650 读取速度 28 GB/s','current',top_k=3)
   self.assertEqual(r['hits'][0]['chunk_id'],'a')
   self.assertIn('hbm3e',m.tokenize('HBM3E'))
   self.assertEqual(idx.search('英伟达 H200','current',top_k=1)['hits'][0]['chunk_id'],'b')
 def test_historical_stats_and_rank_unchanged_by_future_append(self):
  m=self.module('lexical');base=[row('a','memory memory server'),row('b','memory chip')];future=[row('future','memory '*100,'2028-01-01T00:00:00+00:00')]
  with tempfile.TemporaryDirectory() as d:
   one=m.KeywordIndex(Path(d)/'one.db',chunks=base).search('memory','2025-01-01T00:00:00+00:00')
   two=m.KeywordIndex(Path(d)/'two.db',chunks=base+future).search('memory','2025-01-01T00:00:00+00:00')
   self.assertEqual([(h['chunk_id'],h['lexical_bm25']) for h in one['hits']],[(h['chunk_id'],h['lexical_bm25']) for h in two['hits']])
   self.assertEqual(one['eligible_corpus_fingerprint'],two['eligible_corpus_fingerprint'])
   self.assertEqual(two['statistics_scope'],'eligible_as_of_corpus_only')
 def test_mutable_history_and_later_candidates_excluded(self):
  m=self.module('lexical')
  with tempfile.TemporaryDirectory() as d:
   r=m.KeywordIndex(Path(d)/'x.db',chunks=[row('old','memory'),row('mutable','memory',historical=False),row('later','memory','2027-01-01T00:00:00+00:00')]).search('memory','2025-01-01T00:00:00+00:00')
   self.assertEqual([h['chunk_id'] for h in r['hits']],['old'])
 def test_frozen_current_pagination_and_index_namespace(self):
  m=self.module('lexical')
  with tempfile.TemporaryDirectory() as d:
   idx=m.KeywordIndex(Path(d)/'tech.db',chunks=[row(str(n),'memory') for n in range(5)])
   a=idx.search('memory','current',top_k=2);b=idx.search('memory','current',top_k=2,cursor=a['next_cursor'])
   self.assertEqual(a['analysis_time'],b['analysis_time']);self.assertFalse(set(h['chunk_id'] for h in a['hits'])&set(h['chunk_id'] for h in b['hits']))
   self.assertEqual(idx.report['dataset_id'],'tech_company_v1')
   with self.assertRaises(ValueError):idx.search('different','current',top_k=2,cursor=a['next_cursor'])

if __name__=='__main__':unittest.main()
