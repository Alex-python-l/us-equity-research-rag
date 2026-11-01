"""Existing reviewed-card vectors, one local query model/process, fingerprinted cache."""
import json,sqlite3,threading
from contextlib import closing
import numpy as np
from common import ROOT,readl,load,sha
from lexical import eligible

_model=None
_signature=None
_lock=threading.Lock()

def query_vector(query,expected_signature):
 global _model,_signature
 from model import LocalEmbeddingClient
 # Serialize model initialization and inference; never duplicate the 2.2GB session.
 with _lock:
  if _model is None:
   _model=LocalEmbeddingClient(threads=3)
   _signature=sha(json.dumps(_model.provenance(),sort_keys=True))
  if _signature!=expected_signature:raise RuntimeError('query/index embedding fingerprint mismatch')
  key=sha(_signature+'\n'+query)
  with closing(sqlite3.connect(ROOT/'state/tech_query_vectors.sqlite3')) as db,db:
   db.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY,signature TEXT,query TEXT,vector BLOB)')
   old=db.execute('SELECT vector FROM vectors WHERE key=? AND signature=?',(key,_signature)).fetchone()
   if old:return np.frombuffer(old[0],dtype=np.float32).copy(),True
   vector=np.asarray(_model.embed_texts([query])[0],dtype=np.float32)
   db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',(key,_signature,query,vector.tobytes()))
   return vector,False

class DenseRetriever:
 def __init__(self,backend='local',config=None):
  self.backend=backend
  if backend=='neo4j':
   from neo4j_store import read_vector_records
   self.records,self.matrix=read_vector_records(config)
  else:
   self.records=readl(ROOT/'output/embedded_records.jsonl')
   self.matrix=np.load(ROOT/'output/embeddings.npy',allow_pickle=False)
  self.provenance=load(ROOT/'output/model_provenance.json')
  self.signature=sha(json.dumps(self.provenance,sort_keys=True))
  if self.matrix.shape!=(len(self.records),1024):raise RuntimeError('invalid reviewed-card matrix')
  if any(r['embedding_signature']!=self.signature for r in self.records):raise RuntimeError('index/model provenance mismatch')
 def search(self,query,at,historical,top_k=10):
  idx=[i for i,r in enumerate(self.records) if eligible(r,at,historical)]
  if not idx:return {'hits':[],'eligible_records':0,'cache_hit':False,'embedding_signature':self.signature}
  vector,cached=query_vector(query,self.signature)
  scores=self.matrix[idx]@vector
  order=sorted(range(len(idx)),key=lambda j:(-float(scores[j]),self.records[idx[j]]['record_id']))[:top_k]
  return {'hits':[{**self.records[idx[j]],'score':float(scores[j]),'rank':rank+1,'retrieval_channel':'reviewed_dense'} for rank,j in enumerate(order)],
   'eligible_records':len(idx),'cache_hit':cached,'embedding_signature':self.signature,'prefiltered_before_ranking':True,
   'score_interpretation':'cosine relevance; not fact confidence or causal probability'}
