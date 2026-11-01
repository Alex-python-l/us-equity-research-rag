"""Exact source-version and half-open normalized-character interval joins."""
from collections import defaultdict
from common import ROOT,DATASET,load,readl,sha,dump

def location_key(row):
 if row.get('pdf_page') is None and row.get('paragraph') is None:return None
 if not row.get('source_sha256'):return None
 return (row['source_id'],row['source_sha256'],row.get('pdf_page'),row.get('paragraph'))

def build_links(chunks=None,evidence=None,records=None,relations=None):
 chunks=readl(ROOT/'output/chunks.jsonl') if chunks is None else chunks
 evidence=load(ROOT/'output/evidence.json') if evidence is None else evidence
 records=readl(ROOT/'output/embedded_records.jsonl') if records is None else records
 relations=load(ROOT/'output/relations.json') if relations is None else relations
 byloc=defaultdict(list)
 for c in chunks:
  if location_key(c) is not None:byloc[location_key(c)].append(c)
 locators=[];links=[]
 for e in sorted(evidence,key=lambda x:x['evidence_id']):
  start,end=e.get('char_start'),e.get('char_end');intervals=[];key=location_key(e)
  locator_id='TL_'+sha(str(key)+':'+str(start)+':'+str(end))[:24]
  if key is not None and isinstance(start,int) and isinstance(end,int) and end>start:
   for c in sorted(byloc.get(key,[]),key=lambda x:(x['char_start'],x['chunk_id'])):
    lo,hi=max(start,c['char_start']),min(end,c['char_end'])
    if lo<hi:
     intervals.append((lo,hi));links.append({'evidence_id':e['evidence_id'],'locator_id':locator_id,
      'chunk_id':c['chunk_id'],'overlap_start':lo,'overlap_end':hi,'match_method':'source_hash_page_paragraph_interval'})
  merged=[]
  for lo,hi in sorted(intervals):
   if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
   else:merged.append((lo,hi))
  covered=sum(b-a for a,b in merged);span=(end-start) if isinstance(start,int) and isinstance(end,int) and end>start else 0
  locators.append({k:e.get(k) for k in ['evidence_id','source_id','source_sha256','pdf_page','paragraph','char_start','char_end','source_url','available_at','historical_eligible']}|
   {'locator_id':locator_id,'coverage_fraction':covered/span if span else 0,'covered_intervals':merged,
    'coordinate_system':'Python Unicode characters in whitespace-collapsed stripped page/paragraph; half-open [start,end)',
    'status':'exact_chunk_coverage' if span and covered==span else 'locator_only_or_partial'})
 relation_ids={r['relation_id'] for r in relations}
 rlinks=[{'record_id':k['record_id'],'relation_id':rid} for k in sorted(records,key=lambda x:x['record_id']) for rid in k.get('relation_ids',[]) if rid in relation_ids]
 return {'dataset_id':DATASET,'locators':locators,'evidence_chunk_links':links,'record_assertion_links':rlinks,
         'source_version_policy':'exact sha256 and physical page/paragraph; no source-wide or semantic joins'}

if __name__=='__main__':
 result=build_links();dump(ROOT/'output/provenance_links.json',result)
 print({k:len(v) for k,v in result.items() if isinstance(v,list)})
