"""One process, one BGE session: final actual local/Finance verification and demos."""
import argparse,hashlib,json,unittest
from common import ROOT,WORKSPACE,load,dump,now,sha

def run(config):
 from neo4j_store import probe
 from neo4j_links import validate_target,verify_links
 identity=probe(config);validate_target(identity)
 before={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}
 report={'started_at':now(),'finance_verified':True,'database':identity['database'],'no_services_controlled':True}
 suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
 tests=unittest.TextTestRunner(verbosity=2).run(suite)
 report['unit_tests']={'run':tests.testsRun,'passed':tests.wasSuccessful()}
 if not tests.wasSuccessful():raise RuntimeError('unit/regression boundary tests failed')
 from evaluate_research import run as evaluate
 report['evaluation']={}
 for backend in ['local','neo4j']:
  evaluation=evaluate(backend,config if backend=='neo4j' else None)
  report['evaluation'][backend]=evaluation['summary']
 from validate import run as legacy_checks
 for backend in ['local','neo4j']:legacy_checks(backend,config if backend=='neo4j' else None,refresh_news=False)
 from audit_neo4j import run as audit
 audit(config)
 report['graph_links']=verify_links(config)
 from research import ResearchEngine
 from tradingagents_adapter import adapt_evidence_packet
 from agent import run_agent
 questions={'MU':'美光主要业务、HBM供应受限对下游的影响及适用条件是什么？',
  'TSM':'先进封装CoWoS产能变化如何影响下游？区分技术合作与真实供货，并给出条件。',
  'NVDA':'英伟达主要业务、具名直接供应商及HBM和先进封装约束有哪些证据？'}
 examples=[]
 for backend in ['local','neo4j']:
  engine=ResearchEngine(backend,config if backend=='neo4j' else None)
  for ticker,question in questions.items():
   packet=engine.build(ticker,question,'current')
   dump(ROOT/f'output/research_{ticker}_{backend}_20261018.json',packet)
   examples.append({'ticker':ticker,'backend':backend,'status':packet['status'],'citations':len(packet['citations']),
    'assertions':len(packet['assertion_map']),'paths':len(packet['paths']),'fingerprints':packet['fingerprints']})
   if backend=='neo4j' and ticker=='NVDA':dump(ROOT/'output/tradingagents_context_20261018.json',adapt_evidence_packet(packet))
  # Actual state reuse, including a current -> historical turn, both backends.
  sid='release_v2_'+backend
  one=run_agent('MU','SOCAMM2 单CPU容量和带宽','current',sid,backend,config,1,budget={'max_results':6},engine=engine,refresh_news=False)
  two=run_agent('MU','该产品针对哪些系统？','current',sid,backend,config,2,budget={'max_results':6},engine=engine,refresh_news=False)
  historical=run_agent('MU','历史业务单元有哪些','2025-11-01T00:00:00+00:00',sid,backend,config,1,budget={'max_results':6},engine=engine,refresh_news=False)
  assert any(t['tool']=='reuse_verified_context' for t in two['trace'])
  assert 'MU_2026_10K' not in json.dumps(historical,ensure_ascii=False)
  dump(ROOT/f'output/research_agent_{backend}_20261018.json',{'first':one,'followup':two,'historical':historical})
 report['company_examples']=examples
 # All original corpora and document vectors must remain byte-for-byte intact.
 baseline=load(ROOT/'output/baseline_20261018.json');preserved={}
 required=['chunks.jsonl','relations.json','entities.json','evidence.json','source_catalog.json','embedded_records.jsonl','embeddings.npy','model_provenance.json']
 for entry in baseline:
  path=WORKSPACE/entry['path']
  if path.name in required:preserved[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
 assert len(preserved)==len(required) and all(preserved.values())
 after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')}
 if before!=after:raise RuntimeError('production code changed during final verification')
 report.update(original_data_and_document_vectors_preserved=preserved,code_fingerprints=after,completed_at=now(),
  actual_embedding_queries=True,all_chunk_embedding_submitted=False,tradingagents_installed=False,
  lean_deployed=False,broker_connected=False,remote_server_modified=False,llm_generation_executed=False)
 dump(ROOT/'output/release_verification_20261018.json',report)
 print('FINAL_VERIFICATION_COMPLETE',flush=True)
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--config',default=str(ROOT/'.env'))
 run(p.parse_args().config)
