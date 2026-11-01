"""Collect selected accession-versioned filings and their exact SEC acceptance metadata."""
import requests,time,hashlib
from common import *
HEADERS={'User-Agent':'LocalFinancialRAG/1.0 research documentation','Accept-Encoding':'gzip, deflate'}
COMPANIES={'MU':723125,'NVDA':1045810,'TSM':1046179}

def fetch(url,path):
 path=Path(path)
 if path.exists():return path.read_bytes(),200
 r=requests.get(url,headers=HEADERS,timeout=45)
 if r.ok:path.write_bytes(r.content)
 time.sleep(.25)
 return r.content,r.status_code

def main():
 catalog=[];gaps=[]
 for ticker,cik in COMPANIES.items():
  raw,status=fetch(f'https://data.sec.gov/submissions/CIK{cik:010d}.json',ROOT/'raw'/f'{ticker}_submissions.json')
  if status!=200:raise RuntimeError(f'SEC submissions unavailable {ticker}: {status}')
  data=json.loads(raw);recent=data['filings']['recent']
  entries=[{k:recent[k][i] for k in ['form','filingDate','reportDate','acceptanceDateTime','accessionNumber','primaryDocument']}
   for i in range(len(recent['form'])) if recent['filingDate'][i]<=ASOF_DATE]
  annual='20-F' if ticker=='TSM' else '10-K'
  # TSM's last two 20-Fs may be in an older SEC submissions page.
  if sum(r['form']==annual for r in entries)<2:
   for f in data['filings']['files'][:3]:
    body,code=fetch('https://data.sec.gov/submissions/'+f['name'],ROOT/'raw'/f['name'])
    if code==200:
     old=json.loads(body)
     entries.extend({k:old[k][i] for k in ['form','filingDate','reportDate','acceptanceDateTime','accessionNumber','primaryDocument']}
      for i in range(len(old['form'])) if old['filingDate'][i]<=ASOF_DATE)
    if sum(r['form']==annual for r in entries)>=2:break
  entries.sort(key=lambda r:r['acceptanceDateTime'],reverse=True)
  selected=[r for r in entries if r['form']==annual][:2]
  if ticker!='TSM':selected.extend([r for r in entries if r['form']=='10-Q'][:1])
  else:selected.extend([r for r in entries if r['form']=='6-K'][:1])
  # Retain the latest event filings; earnings exhibit selection is reviewed separately.
  event='6-K' if ticker=='TSM' else '8-K'
  selected.extend([r for r in entries if r['form']==event and r not in selected][:2])
  for r in selected:
   accession=r['accessionNumber'];base=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-","")}/'
   sid=f'{ticker}_{accession}'
   item={**r,'source_id':sid,'ticker':ticker,'publisher':'U.S. SEC / '+data['name'],
    'title':f'{ticker} {r["form"]} period {r["reportDate"]}',
    'url':base+r['primaryDocument'],'index_url':base+accession+'-index.html',
    'source_kind':'sec_filing','retrieved_at':now(),'version_group':f'{ticker}_{r["form"]}_{r["reportDate"]}',
    'published_at':r['acceptanceDateTime'],'publication_basis':'SEC submissions acceptanceDateTime; not asserted first global public time',
    'historical_version_basis':'immutable accession-specific filing, original rather than amended accession',
    'historical_version_verified':True,'report_period':r['reportDate'],'local_path':f'raw/{sid}.html'}
   content,code=fetch(item['url'],ROOT/item['local_path'])
   item['http_status']=code;item['access_status']='archived' if code==200 else 'link_only'
   if code==200:item['sha256']=hashlib.sha256(content).hexdigest()
   else:gaps.append({'source_id':sid,'gap':'SEC original document unavailable','http_status':code,'url':item['url']})
   catalog.append(item)
   print(json.dumps({'source_id':sid,'form':r['form'],'period':r['reportDate'],'http':code,'bytes':len(content)}),flush=True)
 dump(ROOT/'output/sec_sources.json',catalog);dump(ROOT/'output/sec_gaps.json',gaps)

if __name__=='__main__':main()
