"""Development regression tests for scope, unsupported detail and literal facets."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import ResearchEngine, technical_coverage
from lexical import KeywordIndex, freeze
from retrieval_v3 import keyword_candidates, query_facets


class RetrievalV4ScopeTests(unittest.TestCase):
 def test_generation_boundary_is_exclusion_without_removing_source_text(self):
  text='SOCAMM was manufactured in 2025.'
  coverage=technical_coverage('说明SOCAMM的量产，并保持与后来的SOCAMM9产品边界。',{'c':{'text':text}})
  self.assertEqual(coverage['unmatched_technical_tokens'],[])
  self.assertEqual(coverage['excluded_technical_tokens'],['SOCAMM9'])
  self.assertEqual(text,'SOCAMM was manufactured in 2025.')

 def test_exclusion_list_ends_before_positive_request(self):
  coverage=technical_coverage('排除HBM4、SOCAMM2；请说明HBM3E。',{'c':{'text':'HBM3E'}})
  self.assertEqual(coverage['requested_technical_tokens'],['HBM3E'])
  self.assertEqual(coverage['excluded_technical_tokens'],['HBM4','SOCAMM2'])
  self.assertEqual(coverage['unmatched_technical_tokens'],[])

 def test_unrecognized_required_generation_remains_insufficient(self):
  coverage=technical_coverage('请给出SOCAMM9的量产信息',{'c':{'text':'SOCAMM2 production'}})
  self.assertEqual(coverage['unmatched_technical_tokens'],['SOCAMM9'])


class RetrievalV4DisclosureTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.engine=ResearchEngine(index_path=':memory:')
  cls.engine.dense_search=lambda *args: (_ for _ in ()).throw(AssertionError('dense prohibited'))

 def packet(self,ticker,question):
  return self.engine.build(ticker,question,'current',{'use_dense':False})

 def test_supplier_generic_risk_cannot_support_complete_contract_price_mapping(self):
  p=self.packet('MU','请提供每一家single or sole source供应商的真实名称、对应物料、每月承诺供货数量和合同单价完整清单。')
  self.assertEqual(p['status'],'insufficient')
  self.assertTrue(p['insufficiency_reasons'])

 def test_indefinite_company_identity_and_exact_contribution_are_insufficient(self):
  p=self.packet('NVDA','年报提及一家通过云服务贡献收入的AI研究公司，它是否就是某具名企业？精确贡献额和占总营收比例是多少？')
  self.assertEqual(p['status'],'insufficient')
  self.assertTrue(p['insufficiency_reasons'])

 def test_disclosure_boundary_question_does_not_request_an_exact_share(self):
  p=self.packet('NVDA','请列出晶圆与内存供应商，并说明证据能否给出各家的供应份额。')
  self.assertNotEqual(p['status'],'insufficient')

 def test_actual_exact_share_request_stays_insufficient(self):
  p=self.packet('NVDA','美光占英伟达HBM采购的精确份额是多少？请给出百分比。')
  self.assertEqual(p['status'],'insufficient')


class RetrievalV4FacetTests(unittest.TestCase):
 def test_generic_supplier_contract_does_not_imply_memory_purchase(self):
  names={p['name'] for p in query_facets('供应商的长期合同能保证产能和价格吗？')}
  self.assertNotIn('memory_purchase',names)
  self.assertIn('supplier_contract',names)

 def test_single_operating_segment_disclosure_is_candidate(self):
  row={'chunk_id':'only','ticker':'TSM','source_id':'source','text':'The Company has only one operating segment, the foundry segment.',
       'available_at':'2024-01-01T00:00:00+00:00','historical_eligible':True}
  idx=KeywordIndex(':memory:',[row]);at,h=freeze('2025-01-01T00:00:00+00:00')
  p=keyword_candidates(idx,'公司经营分部是什么？','TSM',at,h,6,3)
  found=next(x for x in p['hits'] if x['chunk_id']=='only')
  self.assertIn('operating_segments',found['facet_ranks'])

 def test_cloud_customer_revenue_has_a_literal_indirect_disclosure_facet(self):
  row={'chunk_id':'cloud','ticker':'NVDA','source_id':'source',
       'text':'Indirect customers purchase cloud services through our direct customers and contribute revenue.',
       'available_at':'2024-01-01T00:00:00+00:00','historical_eligible':True}
  idx=KeywordIndex(':memory:',[row]);at,h=freeze('2025-01-01T00:00:00+00:00')
  p=keyword_candidates(idx,'一家企业通过云服务带来的收入是多少？','NVDA',at,h,6,3)
  found=next(x for x in p['hits'] if x['chunk_id']=='cloud')
  self.assertIn('indirect_customer',found['facet_ranks'])


if __name__=='__main__':unittest.main()
