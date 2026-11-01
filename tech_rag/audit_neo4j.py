"""Read back actual graph/vector data and run current/historical semantic queries."""
from common import *
from neo4j_store import connect,read_dataset,read_vector_records,INDEX
from tools import CompanyTools
import numpy as np

def run(config):
 checks=[]
 def check(name,ok,details=None):
  checks.append({'name':name,'passed':bool(ok),'details':details})
  if not ok:raise AssertionError(name)
 data=read_dataset(config)
 for key,idkey in [('entities','entity_id'),('relations','relation_id'),('evidence','evidence_id'),('sources','source_id')]:
  local=load(ROOT/'output'/('source_catalog.json' if key=='sources' else key+'.json'))
  check('readback_'+key+'_equals_curated_files',{r[idkey]:r for r in data[key]}=={r[idkey]:r for r in local},len(local))
 driver,info=connect(config)
 try:
  with driver.session(database=info['database']) as s:
   assertions=s.run('MATCH (a:TechAssertion {dataset_id:$d}) WHERE NOT EXISTS {MATCH (a)-[:TECH_SUBJECT]->(:TechEntity {dataset_id:$d})} OR NOT EXISTS {MATCH (a)-[:TECH_OBJECT]->(:TechEntity {dataset_id:$d})} OR NOT EXISTS {MATCH (a)-[:TECH_SUPPORTED_BY]->(:TechEvidence {dataset_id:$d})} RETURN count(a) AS n',d=DATASET).single()['n']
   check('no_assertion_without_endpoints_or_evidence',assertions==0,assertions)
   orphan=s.run('MATCH (e:TechEvidence {dataset_id:$d}) WHERE NOT EXISTS {MATCH (e)-[:TECH_IN_SOURCE]->(:TechSource {dataset_id:$d})} RETURN count(e) AS n',d=DATASET).single()['n']
   check('no_evidence_without_source',orphan==0,orphan)
   materialized=[dict(r) for r in s.run('MATCH (s:TechEntity {dataset_id:$d})-[r {dataset_id:$d}]->(o:TechEntity {dataset_id:$d}) RETURN r.relation_id AS id,type(r) AS kind,s.entity_id AS sub,o.entity_id AS obj,r.evidence_class AS cls,r.fact_edge_eligible AS fact,r.scope AS scope,r.conditions AS conditions',d=DATASET)]
   expected={r['relation_id']:{'id':r['relation_id'],'kind':r['predicate'],'sub':r['subject_id'],'obj':r['object_id'],'cls':r['evidence_class'],'fact':r['fact_edge_eligible'],'scope':r['business_scope'],'conditions':r['conditions']} for r in data['relations']}
   check('typed_relationship_projection_equals_assertions',{r['id']:r for r in materialized}==expected and len(materialized)==len(expected),len(materialized))
   cross=s.run('MATCH (n {dataset_id:$d})-[r]->(m) WHERE m.dataset_id IS NULL OR m.dataset_id<>$d RETURN count(r) AS n',d=DATASET).single()['n']
   check('no_outgoing_cross_dataset_edges',cross==0,cross)
   indexes=[dict(r) for r in s.run('SHOW VECTOR INDEXES YIELD name,state,populationPercent,labelsOrTypes,options WHERE name=$name RETURN *',name=INDEX)]
   check('vector_index_online_and_dedicated',len(indexes)==1 and indexes[0]['state']=='ONLINE' and indexes[0]['labelsOrTypes']==['TechCompanyV1KeyRecord'],indexes)
 finally:driver.close()
 records,vectors=read_vector_records(config)
 local_records=readl(ROOT/'output/embedded_records.jsonl');local_vectors=np.load(ROOT/'output/embeddings.npy',allow_pickle=False)
 local_map={r['record_id']:(r,local_vectors[i]) for i,r in enumerate(local_records)}
 check('actual_neo4j_vectors_match_local_matrix',len(records)==len(local_records) and all(r==local_map[r['record_id']][0] and np.array_equal(vectors[i],local_map[r['record_id']][1]) for i,r in enumerate(records)),list(vectors.shape))
 tool=CompanyTools('neo4j',config)
 current=tool.semantic_evidence('HBM供应受限对NVIDIA和Micron的影响与CoWoS技术依赖','current',6)
 dump(ROOT/'output/semantic_neo4j_demo.json',current)
 check('real_current_query_embedding_and_db_retrieval',bool(current['hits']) and current['backend']=='neo4j' and current['prefiltered_before_ranking'],{'returned':len(current['hits']),'eligible':current['eligible_records']})
 stamp='2026-10-08T23:59:59+00:00'
 historical=tool.semantic_evidence('Micron DRAM NAND业务及NVIDIA memory供应商',stamp,6)
 dump(ROOT/'output/semantic_historical_neo4j_demo.json',historical)
 check('historical_vector_prefilter_excludes_later_disclosures',bool(historical['hits']) and historical['prefiltered_before_ranking'] and all(h['historical_eligible'] and parse_time(h['available_at'])<=parse_time(stamp) and 'MU_2026_10K' not in h['source_ids'] for h in historical['hits']),{'eligible':historical['eligible_records'],'total':historical['total_records']})
 unknown=tool.expand_entity('ASML','current')
 check('unreviewed_new_company_returns_gap',not unknown['found'] and not unknown['relations'],unknown)
 report={'passed':all(c['passed'] for c in checks),'checked_at':now(),'backend':'neo4j','check_count':len(checks),'checks':checks,'db_info':info,'semantic_queries_executed':True,'writes_executed':False,'llm_generation_executed':False}
 dump(ROOT/'output/neo4j_readback_audit.json',report)
 print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False,indent=2))

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);run(p.parse_args().config)
