"""New/old same-budget API. Baseline modules run in an isolated subprocess.

The old engine reads original artifacts through the real common.ROOT and gets
its own lexical index. Dense comparison uses exact previously cached query
vectors only, refusing to start another 2.2GB embedding process.
"""
import json, os, subprocess, sys, time, uuid
from common import ROOT


def baseline_packets(requests,*,timeout=120):
 result=subprocess.run([sys.executable,str(ROOT/'compare_retrieval_v3.py'),'--baseline-worker'],
  input=json.dumps(requests,ensure_ascii=True),capture_output=True,text=True,encoding='utf-8',timeout=timeout,
  env={**os.environ,'PYTHONIOENCODING':'utf-8'})
 if result.returncode:raise RuntimeError('isolated baseline failed: '+result.stderr[-3000:])
 return json.loads(result.stdout)


def compare_packets(requests,*,engine=None):
 """Actual old/new retrieval with identical request budgets; caller owns dense.

Run optimized requests first so any authorized live query embeddings are in
the shared query cache before the isolated baseline reads them. No parameters
are increased. An engine can share one live model and Neo4j snapshot.
"""
 from research import ResearchEngine
 from agent import run_agent
 engine=engine or ResearchEngine();optimized=[];timings=[]
 for request in requests:
  started=time.perf_counter()
  values={k:v for k,v in request.items() if k not in ['agent','max_rounds','backend','config']}
  if request.get('agent'):
   p=run_agent(values['ticker'],values['question'],values['as_of'],'v3_compare_'+uuid.uuid4().hex,
    budget=values.get('budget'),max_rounds=request.get('max_rounds',3),engine=engine,refresh_news=False)['evidence_packet']
  else:p=engine.build(**values)
  optimized.append(p);timings.append(time.perf_counter()-started)
 old=baseline_packets(requests)
 return [{'request':r,'baseline':a,'optimized':b,'optimized_seconds':s,
  'equal_budget':a['budget']==b['budget']} for r,a,b,s in zip(requests,old,optimized,timings)]


def _baseline_worker(requests):
 # common was imported from the live root above. All remaining old retrieval
 # modules resolve from the immutable source backup, including aliases/FTS.
 sys.path.insert(0,str(ROOT/'state/baseline_v3_20261018'))
 from research import ResearchEngine
 from agent import run_agent
 from diagnose_v3 import cached_query_vector
 from unittest.mock import patch
 engines={};packets=[]
 with patch('dense.query_vector',side_effect=cached_query_vector):
  for request in requests:
   backend=request.get('backend','local');config=request.get('config');key=(backend,str(config))
   if key not in engines:engines[key]=ResearchEngine(backend,config,index_path=ROOT/f'state/v3_baseline_compare_{backend}.sqlite3')
   engine=engines[key]
   values={k:v for k,v in request.items() if k not in ['agent','max_rounds','backend','config']}
   if request.get('agent'):
    p=run_agent(values['ticker'],values['question'],values['as_of'],'v3_baseline_'+uuid.uuid4().hex,
     budget=values.get('budget'),max_rounds=request.get('max_rounds',3),engine=engine,refresh_news=False)['evidence_packet']
   else:p=engine.build(**values)
   packets.append(p)
 return packets


if __name__=='__main__' and '--baseline-worker' in sys.argv:
 print(json.dumps(_baseline_worker(json.load(sys.stdin)),ensure_ascii=True))
