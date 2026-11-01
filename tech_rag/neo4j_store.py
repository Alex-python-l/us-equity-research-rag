"""Import only into an explicitly selected existing Neo4j; no DBMS/database creation."""
from common import *
from urllib.parse import urlsplit
from dotenv import dotenv_values
from neo4j import GraphDatabase
import re
import numpy as np

INDEX='tech_bgem3_5617a9f6_1024_v1'

def target(config):
 if not config:raise RuntimeError('Neo4j目标待确认：必须显式指定本地安全配置文件，不自动沿用原油实例')
 path=Path(config).expanduser().resolve()
 if not path.is_file():raise RuntimeError('指定的安全配置文件不存在')
 values=dotenv_values(path,interpolate=False,encoding='utf-8-sig')
 def val(key):return values.get('TECH_'+key) or values.get(key)
 uri=val('NEO4J_URI');db=val('NEO4J_DATABASE');user=val('NEO4J_USER') or val('NEO4J_USERNAME')
 password=val('NEO4J_PASSWORD')
 if not uri or not db or not user or not password:raise RuntimeError('本地配置缺少URI/数据库名/用户名/密码；禁止把凭据写入聊天')
 if db.lower()=='system':raise RuntimeError('system不是业务数据库；请选择Desktop目标实例中已有的业务数据库')
 parsed=urlsplit(uri)
 if parsed.username or parsed.password:raise RuntimeError('URI不能内嵌凭据，请使用单独的安全配置字段')
 if parsed.scheme not in ['bolt','neo4j','bolt+s','neo4j+s','bolt+ssc','neo4j+ssc']:raise RuntimeError('unsupported Neo4j URI scheme')
 info={'uri':uri,'database':db,'config_path':str(path),'credential_values_recorded':False}
 return info,user,password

def connect(config):
 info,user,password=target(config)
 driver=GraphDatabase.driver(info['uri'],auth=(user,password),connection_timeout=10)
 driver.verify_connectivity()
 return driver,info

def probe(config):
 driver,info=connect(config)
 try:
  with driver.session(database=info['database']) as s:
   components=[dict(r) for r in s.run('CALL dbms.components() YIELD name,versions,edition RETURN *')]
   count=s.run('MATCH (n) RETURN count(n) AS n').single()['n']
   settings=[dict(r) for r in s.run('SHOW SETTINGS YIELD name,value WHERE name IN $names RETURN name,value',
    names=['server.directories.neo4j_home','server.bolt.listen_address','server.routing.listen_address','server.routing.advertised_address'])]
   databases=[dict(r) for r in s.run('SHOW DATABASES YIELD name,currentStatus RETURN name,currentStatus')]
  return {**info,'reachable':True,'components':components,'total_nodes':count,'connection_settings':settings,'existing_databases':databases,'writes_executed':False}
 finally:driver.close()

def data_snapshot():
 return {'entities':load(ROOT/'output/entities.json'),'relations':load(ROOT/'output/relations.json'),
  'evidence':load(ROOT/'output/evidence.json'),'sources':load(ROOT/'output/source_catalog.json')}

def payload(row):
 return {'payload':json.dumps(row,ensure_ascii=False),'dataset_id':DATASET}

def upsert(s,label,key,rows):
 allowed={'TechEntity':'entity_id','TechAssertion':'relation_id','TechEvidence':'evidence_id','TechSource':'source_id','TechChunk':'chunk_id','TechKeyRecord':'record_id'}
 if allowed.get(label)!=key:raise ValueError('unexpected graph schema')
 for i in range(0,len(rows),100):
  s.run(f'UNWIND $rows AS row MERGE (n:{label} {{{key}:row.{key},dataset_id:$dataset}}) SET n += row',rows=rows[i:i+100],dataset=DATASET).consume()

def import_dataset(config,confirmed=False):
 if not confirmed:raise RuntimeError('用户尚未确认现有Neo4j连接目标；不执行写入')
 dataset=data_snapshot();matrix=np.load(ROOT/'output/embeddings.npy',allow_pickle=False)
 records=readl(ROOT/'output/embedded_records.jsonl');chunks=readl(ROOT/'output/chunks.jsonl')
 if matrix.shape!=(len(records),1024) or not np.isfinite(matrix).all():raise RuntimeError('invalid embedding matrix')
 model=load(ROOT/'output/model_provenance.json');signature=sha(json.dumps(model,sort_keys=True))
 if any(r['embedding_signature']!=signature for r in records):raise RuntimeError('embedding signature mismatch')
 fingerprint=sha(json.dumps(dataset,sort_keys=True,ensure_ascii=False))
 driver,info=connect(config)
 try:
  with driver.session(database=info['database']) as s:
   before=s.run("MATCH (n) WHERE none(l IN labels(n) WHERE l STARTS WITH 'Tech') RETURN count(n) AS n").single()['n']
   for label,key in [('TechEntity','entity_id'),('TechAssertion','relation_id'),('TechEvidence','evidence_id'),('TechSource','source_id'),('TechChunk','chunk_id'),('TechKeyRecord','record_id')]:
    s.run(f'CREATE CONSTRAINT {label.lower()}_dataset_id_v1 IF NOT EXISTS FOR (n:{label}) REQUIRE (n.dataset_id,n.{key}) IS UNIQUE').consume()
   s.run('MERGE (d:TechDataset {dataset_id:$dataset}) SET d.import_status="in_progress",d.updated_at=$now',dataset=DATASET,now=now()).consume()
   entities=[{**r,**payload(r)} for r in dataset['entities']];upsert(s,'TechEntity','entity_id',entities)
   s.run('MATCH (e:TechEntity {dataset_id:$dataset,kind:"Company"}) SET e:TechCompany',dataset=DATASET).consume()
   upsert(s,'TechSource','source_id',[{'source_id':r['source_id'],'title':r['title'],'url':r['url'],'sha256':r.get('sha256'),
    'available_at':r.get('available_at'),'published_at':r.get('published_at'),'published_date':r.get('published_date'),
    'report_period':r.get('report_period'),'accession':r.get('accession'),**payload(r)} for r in dataset['sources']])
   upsert(s,'TechEvidence','evidence_id',[{**r,**payload(r)} for r in dataset['evidence']])
   upsert(s,'TechAssertion','relation_id',[{k:v for k,v in {**r,**payload(r)}.items() if not isinstance(v,dict)} for r in dataset['relations']])
   s.run('UNWIND $rows AS row MATCH (a:TechAssertion {relation_id:row.relation_id,dataset_id:$dataset}), '
    '(sub:TechEntity {entity_id:row.subject_id,dataset_id:$dataset}),(obj:TechEntity {entity_id:row.object_id,dataset_id:$dataset}) '
    'MERGE (a)-[:TECH_SUBJECT]->(sub) MERGE (a)-[:TECH_OBJECT]->(obj)',rows=dataset['relations'],dataset=DATASET).consume()
   for r in dataset['relations']:
    kind=r['predicate']
    if not re.fullmatch('[A-Z][A-Z_]{1,50}',kind):raise ValueError('unsafe predicate')
    s.run(f'MATCH (a:TechEntity {{entity_id:$sub,dataset_id:$dataset}}),(b:TechEntity {{entity_id:$obj,dataset_id:$dataset}}) '
     f'MERGE (a)-[r:{kind} {{relation_id:$id,dataset_id:$dataset}}]->(b) '
     'SET r.evidence_class=$cls,r.directness=$directness,r.scope=$scope,r.conditions=$conditions,r.available_at=$at,r.fact_edge_eligible=$fact',
     sub=r['subject_id'],obj=r['object_id'],id=r['relation_id'],dataset=DATASET,cls=r['evidence_class'],
     directness=r['directness'],scope=r['business_scope'],conditions=r['conditions'],at=r['available_at'],fact=r['fact_edge_eligible']).consume()
   pairs=[{'relation_id':r['relation_id'],'evidence_id':eid} for r in dataset['relations'] for eid in r['evidence_ids']]
   s.run('UNWIND $rows AS row MATCH (a:TechAssertion {relation_id:row.relation_id,dataset_id:$dataset}),(e:TechEvidence {evidence_id:row.evidence_id,dataset_id:$dataset}) MERGE (a)-[:TECH_SUPPORTED_BY]->(e)',rows=pairs,dataset=DATASET).consume()
   s.run('UNWIND $rows AS row MATCH (e:TechEvidence {evidence_id:row.evidence_id,dataset_id:$dataset}),(s:TechSource {source_id:row.source_id,dataset_id:$dataset}) MERGE (e)-[:TECH_IN_SOURCE]->(s)',rows=dataset['evidence'],dataset=DATASET).consume()
   upsert(s,'TechChunk','chunk_id',[{'chunk_id':r['chunk_id'],'source_id':r['source_id'],'text':r['text'],
    'pdf_page':r['pdf_page'],'paragraph':r['paragraph'],'section':r['section'],'available_at':r['available_at'],**payload(r)} for r in chunks])
   for start in range(0,len(chunks),300):
    s.run('UNWIND $rows AS row MATCH (c:TechChunk {chunk_id:row.chunk_id,dataset_id:$dataset}),(s:TechSource {source_id:row.source_id,dataset_id:$dataset}) MERGE (s)-[:TECH_HAS_CHUNK]->(c)',rows=chunks[start:start+300],dataset=DATASET).consume()
   upsert(s,'TechKeyRecord','record_id',[{'record_id':r['record_id'],'kind':r['kind'],'text':r['text'],'embedding':matrix[i].tolist(),
    'embedding_signature':signature,'available_at':r['available_at'],'historical_eligible':r['historical_eligible'],**payload(r)} for i,r in enumerate(records)])
   record_links=[{'record_id':r['record_id'],'evidence_id':eid} for r in records for eid in r['evidence_ids']]
   s.run('UNWIND $rows AS row MATCH (k:TechKeyRecord {record_id:row.record_id,dataset_id:$dataset}),(e:TechEvidence {evidence_id:row.evidence_id,dataset_id:$dataset}) MERGE (k)-[:TECH_REFERS_TO]->(e)',rows=record_links,dataset=DATASET).consume()
   s.run('MATCH (k:TechKeyRecord {dataset_id:$dataset}) SET k:TechCompanyV1KeyRecord',dataset=DATASET).consume()
   s.run(f'CREATE VECTOR INDEX {INDEX} IF NOT EXISTS FOR (k:TechCompanyV1KeyRecord) ON (k.embedding) '
    'OPTIONS {indexConfig:{`vector.dimensions`:1024,`vector.similarity_function`:"cosine"}}').consume()
   s.run('CALL db.awaitIndex($name,120)',name=INDEX).consume()
   after=s.run("MATCH (n) WHERE none(l IN labels(n) WHERE l STARTS WITH 'Tech') RETURN count(n) AS n").single()['n']
   counts={label:s.run(f'MATCH (n:{label} {{dataset_id:$dataset}}) RETURN count(n) AS n',dataset=DATASET).single()['n']
    for label in ['TechEntity','TechSource','TechAssertion','TechEvidence','TechChunk','TechKeyRecord']}
   index=[dict(r) for r in s.run('SHOW VECTOR INDEXES YIELD name,state,populationPercent WHERE name=$name RETURN *',name=INDEX)]
   hits=[dict(r) for r in s.run('CALL db.index.vector.queryNodes($name,5,$v) YIELD node,score RETURN node.record_id AS record_id,score',name=INDEX,v=matrix[0].tolist())]
   assert any(h['record_id']==records[0]['record_id'] and h['score']>.9999 for h in hits)
   assert index[0]['state']=='ONLINE' and counts['TechAssertion']==len(dataset['relations'])
   assert before==after,'non-Tech node count changed; investigate concurrent writes'
   s.run('MATCH (d:TechDataset {dataset_id:$dataset}) SET d.import_status="complete",d.fingerprint=$fingerprint,'
    'd.model_signature=$model,d.updated_at=$now',dataset=DATASET,fingerprint=fingerprint,model=signature,now=now()).consume()
  report={**info,'dataset_id':DATASET,'fingerprint':fingerprint,'node_counts':counts,'vector_index':index,
   'self_match':hits,'non_tech_nodes_before':before,'non_tech_nodes_after':after,'old_node_counts_preserved':before==after,
   'executed':True,'new_dbms_created':False,'new_database_created':False,'delete_queries_executed':False,'imported_at':now()}
  dump(ROOT/'output/neo4j_import_report.json',report);return report
 finally:driver.close()

def read_dataset(config):
 driver,info=connect(config)
 try:
  with driver.session(database=info['database']) as s:
   status=s.run('MATCH (d:TechDataset {dataset_id:$dataset}) RETURN d.import_status AS status',dataset=DATASET).single()
   if not status or status['status']!='complete':raise RuntimeError('existing target has no complete Tech dataset; do not silently fall back to local files')
   result={}
   for name,label,key in [('entities','TechEntity','entity_id'),('relations','TechAssertion','relation_id'),('evidence','TechEvidence','evidence_id'),('sources','TechSource','source_id')]:
    rows=[];offset=0
    while True:
     page=[json.loads(r['payload']) for r in s.run(f'MATCH (n:{label} {{dataset_id:$dataset}}) RETURN n.payload AS payload ORDER BY n.{key} SKIP $offset LIMIT 500',dataset=DATASET,offset=offset)]
     rows.extend(page);offset+=len(page)
     if len(page)<500:break
     if offset>=10000:raise RuntimeError('dataset load cap reached; explicit larger backend pagination required')
    result[name]=rows
  return result
 finally:driver.close()

def read_vector_records(config):
 driver,info=connect(config)
 try:
  records=[];vectors=[];offset=0
  with driver.session(database=info['database']) as s:
   while True:
    rows=list(s.run('MATCH (k:TechKeyRecord {dataset_id:$dataset}) RETURN k.payload AS payload,k.embedding AS embedding '
     'ORDER BY k.record_id SKIP $offset LIMIT 500',dataset=DATASET,offset=offset))
    for row in rows:records.append(json.loads(row['payload']));vectors.append(row['embedding'])
    offset+=len(rows)
    if len(rows)<500:break
    if offset>=10000:raise RuntimeError('vector scan cap reached; explicit larger bounded retrieval required')
  if not records:raise RuntimeError('target has no embedded Tech records')
  return records,np.asarray(vectors,dtype=np.float32)
 finally:driver.close()
