"""Tech-only persistent documents; FTS5 BM25 rebuilt on the eligible time slice.

Adapts the tokenization/RRF idea in financial_rag.hybrid, without importing its
oil state. Statistics, candidates, ties and pagination see ONLY allowed documents.
"""
import base64,json,re,sqlite3,threading
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from common import ROOT,DATASET,readl,sha,now,parse_time
from aliases import expand_query

TOKENIZER_VERSION='fixed-morphology-v3'
_MORPHOLOGY={'foundries':'foundry','suppliers':'supplier','customers':'customer','partners':'partner',
 'wafers':'wafer','segments':'segment','technologies':'technology','manufactured':'manufacture',
 'manufacturing':'manufacture','purchased':'purchase','purchases':'purchase','purchasing':'purchase',
 'collaborating':'collaborate','collaboration':'collaborate','collaborations':'collaborate',
 'qualified':'qualification','qualifications':'qualification','interconnects':'interconnect',
 'capacitors':'capacitor','constraints':'constraint','constrained':'constraint'}

def tokenize(text):
 out=[]
 for t in re.findall(r'[a-zA-Z0-9]+(?:[.\-/][a-zA-Z0-9]+)*|[\u3400-\u9fff]+',text.lower()):
  if re.search(r'[\u3400-\u9fff]',t):
   out.extend(t[i:i+2] for i in range(len(t)-1))
   if len(t)==1:out.append(t)
  else:
   out.append(t)
   if t in _MORPHOLOGY:out.append(_MORPHOLOGY[t])
   if re.search(r'[.\-/]',t):out.extend(re.findall(r'[a-z0-9]+',t));out.append(re.sub(r'\W','',t))
 return out

def eligible(row,at,historical):
 try:return parse_time(row['available_at'])<=at and (not historical or row.get('historical_eligible',False))
 except (KeyError,ValueError,TypeError):return False

def freeze(as_of):
 if not as_of:raise ValueError('as_of is required (timezone ISO or current)')
 return (parse_time(now()),False) if as_of=='current' else (parse_time(as_of),True)

def encode_cursor(obj):return base64.urlsafe_b64encode(json.dumps(obj,sort_keys=True).encode()).decode().rstrip('=')
def decode_cursor(value):
 try:return json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))
 except Exception as e:raise ValueError('invalid cursor') from e

class KeywordIndex:
 def __init__(self,path=None,chunks=None):
  self.path=Path(path or ROOT/'state/tech_lexical.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
  self.rows=list(chunks) if chunks is not None else readl(ROOT/'output/chunks.jsonl')
  self.rows.sort(key=lambda x:x['chunk_id']);self.by_id={x['chunk_id']:x for x in self.rows}
  if len(self.by_id)!=len(self.rows):raise ValueError('duplicate chunk IDs')
  self.fingerprint=sha(json.dumps(self.rows,sort_keys=True,ensure_ascii=False))
  with closing(sqlite3.connect(self.path)) as db, db:
   db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY,payload TEXT,tokens TEXT)')
   old=dict(db.execute('SELECT key,value FROM meta'))
   if old.get('dataset_id') not in (None,DATASET):raise ValueError('refusing non-Tech keyword index')
   if old.get('fingerprint')!=self.fingerprint or old.get('tokenizer_version')!=TOKENIZER_VERSION:
    db.execute('DELETE FROM documents') # only this explicitly tech-only local index
    db.executemany('INSERT INTO documents VALUES (?,?,?)',[(r['chunk_id'],json.dumps(r,ensure_ascii=False),' '.join(tokenize(r.get('text','')))) for r in self.rows])
    db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)',[('dataset_id',DATASET),('fingerprint',self.fingerprint),('tokenizer_version',TOKENIZER_VERSION)])
   self.tokens=dict(db.execute('SELECT id,tokens FROM documents'))
  self.report={'dataset_id':DATASET,'chunks':len(self.rows),'data_fingerprint':self.fingerprint,'path':str(self.path),
   'algorithm':'SQLite FTS5 BM25 on eligible_as_of corpus; raw BM25 negative, smaller is more relevant',
   'statistics_scope':'eligible_as_of_corpus_only','all_chunk_embedding_submitted':False}
  # Official LangGraph ToolNode workers can change between runs. SQLite FTS
  # connections and per-query SQL functions must remain on their owning thread;
  # sharing a connection with check_same_thread=False would also race ticker
  # scoring callbacks. Share immutable rows/tokens, not live connections.
  self._snapshot_local=threading.local()

 def snapshot(self,at,historical):
  cache=getattr(self._snapshot_local,'cache',None)
  if cache is None:cache=OrderedDict();self._snapshot_local.cache=cache
  rows=[r for r in self.rows if eligible(r,at,historical)]
  fp=sha(json.dumps(rows,sort_keys=True,ensure_ascii=False))
  if fp not in cache:
   db=sqlite3.connect(':memory:')
   db.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(id UNINDEXED,tokens,tokenize='unicode61')")
   db.executemany('INSERT INTO documents_fts VALUES (?,?)',[(r['chunk_id'],self.tokens[r['chunk_id']]) for r in rows])
   cache[fp]=(db,rows)
   if len(cache)>4:cache.popitem(last=False)[1][0].close()
  else:cache.move_to_end(fp)
  return fp,cache[fp]

 def search(self,query,as_of,top_k=10,cursor=None,*,at=None,historical=None):
  if not isinstance(top_k,int) or not 1<=top_k<=100:raise ValueError('top_k must be 1..100')
  if not isinstance(query,str) or not query.strip() or len(query)>8000:raise ValueError('query must contain 1..8000 characters')
  at,mode=(at,historical) if at is not None else freeze(as_of)
  offset=0;obj=None
  if cursor:
   obj=decode_cursor(cursor)
   if as_of=='current' and at is not None:at=parse_time(obj['analysis_time'])
  fp,(db,rows)=self.snapshot(at,mode)
  key=sha(json.dumps([query,at.isoformat(),mode,fp],ensure_ascii=False))
  if obj:
   if obj.get('query_key')!=key:raise ValueError('cursor/query/time/corpus mismatch')
   offset=obj.get('offset')
   if not isinstance(offset,int) or offset<0:raise ValueError('invalid cursor offset')
  tokens=list(dict.fromkeys(tokenize(expand_query(query))))[:160]
  match=' OR '.join('"'+t.replace('"','""')+'"' for t in tokens)
  total=db.execute('SELECT count(*) FROM documents_fts WHERE documents_fts MATCH ?',(match,)).fetchone()[0] if match else 0
  found=db.execute('SELECT id,bm25(documents_fts) AS score FROM documents_fts WHERE documents_fts MATCH ? ORDER BY score,id LIMIT ? OFFSET ?',
   (match,top_k,offset)).fetchall() if total else []
  hits=[{**self.by_id[id],'lexical_bm25':float(score),'rank':offset+i+1,'retrieval_channel':'keyword',
         'review_status':'raw_original_unreviewed_as_relation'} for i,(id,score) in enumerate(found)]
  next_offset=offset+len(hits)
  return {'hits':hits,'analysis_time':at.isoformat(),'temporal_mode':'strict_historical' if mode else 'current_as_collected',
   'eligible_corpus_fingerprint':fp,'statistics_scope':'eligible_as_of_corpus_only','eligible_chunks':len(rows),
   'matched_chunks':total,'offset':offset,'next_cursor':encode_cursor({'query_key':key,'offset':next_offset,'analysis_time':at.isoformat()}) if next_offset<total else None,
   'truncated':next_offset<total,'query_terms':tokens,'score_interpretation':'relevance only; not confidence or causality'}
