import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class BoundaryTests(unittest.TestCase):
 def test_keyword_candidate_budget_over_one_batch(self):
  from research import ResearchEngine
  from common import ROOT,readl
  rows=readl(ROOT/'output/chunks.jsonl')[:1]
  rows=[{**rows[0],'chunk_id':f'test_{i:04}','text':'memory'} for i in range(250)]
  engine=ResearchEngine(chunks=rows,index_path=':memory:')
  # Synthetic offsets need not pass original verification; this tests the
  # independent pre-verification candidate budget, not quote correctness.
  p=engine.build('MU','memory','current',{'use_dense':False,'keyword_candidates':150,'max_tool_calls':4})
  hit=next(t for t in p['trace'] if t['tool']=='keyword_search')
  self.assertEqual(hit['hits'],150)
 def test_future_dense_card_cannot_change_historical_scores(self):
  import numpy as np
  from dense import DenseRetriever
  from common import parse_time
  records=[{'record_id':'a','available_at':'2024-01-01T00:00:00+00:00','historical_eligible':True},
           {'record_id':'b','available_at':'2024-01-01T00:00:00+00:00','historical_eligible':True}]
  future={'record_id':'f','available_at':'2028-01-01T00:00:00+00:00','historical_eligible':True}
  def make(rows,matrix):
   d=DenseRetriever.__new__(DenseRetriever);d.records=rows;d.matrix=np.asarray(matrix,dtype=np.float32);d.signature='test';return d
  one=make(records,[[1,0],[0.8,0.6]]);two=make(records+[future],[[1,0],[0.8,0.6],[1,0]])
  with patch('dense.query_vector',return_value=(np.asarray([1,0],dtype=np.float32),False)):
   a=one.search('memory',parse_time('2025-01-01T00:00:00+00:00'),True,10)
   b=two.search('memory',parse_time('2025-01-01T00:00:00+00:00'),True,10)
   self.assertEqual(a,b)
 def test_unknown_technical_model_is_insufficient_not_similar_products(self):
  from research import ResearchEngine
  p=ResearchEngine().build('MU','HBM99 512GB mass production details','current',{'use_dense':False})
  self.assertEqual(p['status'],'insufficient')
  self.assertTrue(p['question_coverage']['unmatched_technical_tokens'])

if __name__=='__main__':unittest.main()
