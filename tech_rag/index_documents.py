"""Section/page-bounded semantic chunks; prioritize reviewed relation/evidence vectors."""
from common import *
import re,sqlite3,os,contextlib
import numpy as np
from model import LocalEmbeddingClient,tokenizer,MAX_INPUT_TOKENS

def windows(text,tok,budget=280,overlap=32):
 e=tok.encode(text,add_special_tokens=False);offsets=e.offsets;i=0
 while i<len(offsets):
  end=min(i+budget,len(offsets))
  if end<len(offsets):
   for j in range(end-1,i+budget*3//4,-1):
    a,b=offsets[j]
    if re.search(r'[.!?;。；\n]',text[a:b]):end=j+1;break
  a,b=offsets[i][0],offsets[end-1][1]
  yield text[a:b],a,b
  if end==len(offsets):break
  i=max(i+1,end-overlap)

def main():
 lock=ROOT/'state/embedding.lock'
 try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 except FileExistsError:raise RuntimeError('已有embedding锁；先核实任务状态，不重复提交')
 os.write(fd,json.dumps({'pid':os.getpid(),'started_at':now()}).encode());os.close(fd)
 try:run()
 finally:lock.unlink(missing_ok=True)

def run():
 tok=tokenizer();sources=load(ROOT/'output/source_catalog.json')
 evs=load(ROOT/'output/evidence.json');rels=load(ROOT/'output/relations.json')
 entities={e['entity_id']:e for e in load(ROOT/'output/entities.json')}
 chunks=[]
 for s in sources:
  if s.get('include_in_rag') is False:continue
  if not s.get('text_path') or not (ROOT/s['text_path']).exists():continue
  rows=readl(ROOT/s['text_path']);start=1
  if s['kind']=='annual_bundle':
   covers=[r['pdf_page'] for r in rows if r.get('pdf_page') and
    re.search(r'UNITED STATES\s+SECURITIES AND EXCHANGE COMMISSION',r['text'],re.I) and 'FORM 10-K' in r['text']]
   if covers:start=min(covers)
   else:s['processing_gap']='10-K cover boundary not verified; bundle excluded';continue
   s['processed_pdf_range']=[start,s.get('page_count')]
  if s['source_id']=='MU_2026_10K':s['processed_pdf_range']=[1,105]
  for block in rows:
   page=block.get('pdf_page')
   if page and (page<start or page>s.get('processed_pdf_range',[1,10**9])[1]):continue
   normalized=re.sub(r'\s+',' ',block['text']).strip()
   # Each semantically bounded unit remains within its physical PDF page/HTML block.
   boundaries=[0]+[m.end() for m in re.finditer(r'(?<=[.!?])\s+(?=[A-Z(])',normalized)]+[len(normalized)]
   sentences=[(boundaries[i],boundaries[i+1]) for i in range(len(boundaries)-1) if boundaries[i]<boundaries[i+1]]
   grouped=[];a=0;b=0
   for sa,sb in sentences:
    if b and len(tok.encode(normalized[a:sb],add_special_tokens=False).ids)>280:
     grouped.append((a,b));a=sa
    b=sb
   if b:grouped.append((a,b))
   section=block.get('section_hint') or 'Page-bounded filing text'
   same_page=[e for e in evs if e['source_id']==s['source_id'] and e['pdf_page']==page and e['paragraph']==block.get('paragraph')]
   if same_page:section=' / '.join(dict.fromkeys(e['section'] for e in same_page))
   for a,b in grouped:
    for text,ca,cb in windows(normalized[a:b],tok):
     location={'pdf_page':page,'paragraph':block.get('paragraph'),'char_start':a+ca,'char_end':a+cb}
     cid='TC_'+sha(s['source_id']+json.dumps(location,sort_keys=True)+text)[:22]
     chunks.append({'chunk_id':cid,'source_id':s['source_id'],'ticker':s['ticker'],'section':section,
      **location,'text':text,'source_sha256':s['sha256'],'source_url':s['url'],
      'available_at':s['available_at'],'historical_eligible':s['historical_eligible'],
      'report_period':s.get('report_period'),'table_policy':block.get('table_policy'),
      'source_kind':s['kind'],'semantic_chunking':'sentence groups, <=280 body tokens, overflow token windows; never crosses page/block'})
 jsonl(ROOT/'output/chunks.jsonl',chunks);dump(ROOT/'output/source_catalog.json',sources)
 # Key records are separate from arbitrary financial paragraphs and keep source-specific time gates.
 records=[]
 for e in evs:
  prefix=f"{e['source_id']} | {e['section']}\n"
  # Bound the prefix independently so no evidence is silently cut.
  pre=tok.encode(prefix,add_special_tokens=False)
  if len(pre.ids)>40:prefix=prefix[:pre.offsets[39][1]]+'\n'
  for i,(part,a,b) in enumerate(windows(e['quote'],tok),1):
   records.append({'record_id':e['evidence_id']+f'_{i}','kind':'reviewed_evidence','evidence_ids':[e['evidence_id']],
    'relation_ids':[],'text':prefix+part,'available_at':e['available_at'],
    'historical_eligible':e['historical_eligible'],'source_ids':[e['source_id']]})
 for r in rels:
  text=f"{entities[r['subject_id']]['name']} — {r['predicate']} → {entities[r['object_id']]['name']}。{r['claim']}\n业务范围：{r['business_scope']}\n条件：{r['conditions']}\n类别：{r['evidence_class']}；{r['directness']}"
  for i,(part,a,b) in enumerate(windows(text,tok,budget=350),1):
   records.append({'record_id':r['relation_id']+f'_{i}','kind':'reviewed_relation','evidence_ids':r['evidence_ids'],
    'relation_ids':[r['relation_id']],'text':part,'available_at':r['available_at'],
    'historical_eligible':r['historical_eligible'],'source_ids':r['source_ids']})
 assert max(len(tok.encode(r['text']).ids) for r in records)<=MAX_INPUT_TOKENS
 jsonl(ROOT/'output/key_records.jsonl',records)
 print(json.dumps({'stage':'prepared','chunks':len(chunks),'priority_records':len(records)},ensure_ascii=False),flush=True)
 model=LocalEmbeddingClient(threads=3);provenance=model.provenance()
 signature=sha(json.dumps(provenance,sort_keys=True));dump(ROOT/'output/model_provenance.json',provenance)
 cache=sqlite3.connect(ROOT/'state/embedding_cache.sqlite3')
 cache.execute('CREATE TABLE IF NOT EXISTS embeddings (cache_key TEXT PRIMARY KEY, vector BLOB NOT NULL)')
 vectors=[];hits=0
 for i in range(0,len(records),2):
  batch=records[i:i+2];vv=[None]*len(batch);todo=[]
  for j,r in enumerate(batch):
   key=sha(signature+r['text']);old=cache.execute('SELECT vector FROM embeddings WHERE cache_key=?',(key,)).fetchone()
   if old:vv[j]=np.frombuffer(old[0],dtype=np.float32).copy();hits+=1
   else:todo.append((j,key,r))
  if todo:
   calculated=model.embed_texts([r['text'] for _,_,r in todo])
   for (j,key,r),v in zip(todo,calculated,strict=True):
    vv[j]=np.asarray(v,dtype=np.float32)
    cache.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?)',(key,vv[j].tobytes()))
   cache.commit()
  vectors.extend(vv)
  if i%20==0:print(json.dumps({'stage':'embedding','done':min(i+2,len(records)),'total':len(records),'cache_hits':hits}),flush=True)
 cache.close();matrix=np.asarray(vectors,dtype=np.float32)
 assert matrix.shape==(len(records),1024) and np.isfinite(matrix).all()
 assert np.allclose(np.linalg.norm(matrix,axis=1),1,atol=1e-5)
 np.save(ROOT/'output/embeddings.npy',matrix,allow_pickle=False)
 for r in records:r.update(embedding_signature=signature,embedding_model='BAAI/bge-m3',dimensions=1024,text_sha256=sha(r['text']))
 jsonl(ROOT/'output/embedded_records.jsonl',records)
 dump(ROOT/'output/index_report.json',{'source_documents':len(sources),'text_chunks':len(chunks),'embedded_records':len(records),
  'embedding_dimensions':1024,'cache_hits':hits,'relation_cards':sum(r['kind']=='reviewed_relation' for r in records),
  'reviewed_evidence_records':sum(r['kind']=='reviewed_evidence' for r in records),'all_chunks_embedded':False,
  'training_executed':False,'numeric_time_series_embedded':False,'model_signature':signature})
 print('PASS: key relation/evidence embeddings persisted',flush=True)

if __name__=='__main__':main()
