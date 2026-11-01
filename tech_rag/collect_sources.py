"""Issuer-hosted report copies, partnership announcements, technology and policy originals."""
from common import *
import requests,hashlib,re,time
from bs4 import BeautifulSoup
import fitz

SEEDS=[
('MU_2026_10K','MU','annual','Micron FY2026 Form 10-K','https://d18rn0p25nwr6d.cloudfront.net/CIK-0000723125/5dafce68-38e8-4cad-a8f3-ab56d04d73fd.pdf','2026-09-03','2026-10-09','0000723125-26-000023'),
('MU_2025_10K','MU','annual','Micron FY2025 Form 10-K','https://s25.q4cdn.com/621799436/files/doc_financials/2025/ar/2025-Form-10-K.pdf','2025-08-28','2025-10-03','0000723125-25-000028'),
('MU_2026_Q3','MU','quarterly','Micron FY2026 Q3 Form 10-Q','https://investors.micron.com/static-files/23023765-dfef-4e7e-845b-cd744fc20d93','2026-05-28','2026-06-25','0000723125-26-000015'),
('MU_2026_Q4_REMARKS','MU','quarterly_update','Micron FY2026 Q4 prepared remarks','https://s25.q4cdn.com/621799436/files/doc_financials/2026/q4/Q4-FY26-Prepared-Remarks.pdf','2026-09-03','2026-09-30',None),
('NVDA_2026_AR','NVDA','annual_bundle','NVIDIA 2026 annual report / proxy / 10-K bundle','https://s201.q4cdn.com/141608511/files/doc_financials/2026/ar/2026-Annual-Report-Web.pdf','2026-01-25','2026-05-12','0001045810-26-000021'),
('NVDA_2025_AR','NVDA','annual_bundle','NVIDIA 2025 annual report / proxy / 10-K bundle','https://s201.q4cdn.com/141608511/files/doc_financials/2025/annual/NVIDIA-2025-Annual-Report.pdf','2025-01-26','2025-05-13','0001045810-25-000023'),
('NVDA_2026_10K','NVDA','annual','NVIDIA FY2026 Form 10-K standalone','https://s201.q4cdn.com/141608511/files/doc_financials/2026/q4/10K-NVDA.pdf','2026-01-25','2026-02-25','0001045810-26-000021'),
('NVDA_2025_10K','NVDA','annual','NVIDIA FY2025 Form 10-K standalone','https://s201.q4cdn.com/141608511/files/doc_financials/2025/q4/177440d5-3b32-4185-8cc8-95500a9dc783.pdf','2025-01-26','2025-02-26','0001045810-25-000023'),
('NVDA_2027_Q2_10Q','NVDA','quarterly','NVIDIA FY2027 Q2 Form 10-Q','https://s201.q4cdn.com/141608511/files/doc_financials/2027/NVDA-2027-Q2-10Q-Final-including-exhibits.pdf','2026-07-26','2026-08-26','0001045810-26-000075'),
('NVDA_2027_Q2_RELEASE','NVDA','quarterly_update','NVIDIA FY2027 Q2 earnings release','https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/default.aspx','2026-07-26','2026-08-26','0001045810-26-000073'),
('NVDA_2027_Q2_CALL','NVDA','corrected_transcript','NVIDIA FY2027 Q2 corrected earnings transcript','https://investor.nvidia.com/files/content_files/TRANSCRIPT_-NVIDIA-Corp-NVDA-US-Q2-2027-Earnings-Call-26-August-2026-5_00-PM-ET.pdf','2026-07-26','2026-08-26',None),
('TSM_2025_20F','TSM','annual','TSMC 2025 Form 20-F','https://investor.tsmc.com/sites/ir/sec-filings/2025_20F%20Report.pdf','2025-12-31','2026-04-17','0001628280-26-025362'),
('TSM_2024_20F','TSM','annual','TSMC 2024 Form 20-F','https://investor.tsmc.com/sites/ir/sec-filings/2024%2020-F.pdf','2024-12-31','2025-04-17','0001193125-25-083423'),
('TSM_2026_Q3_CALL','TSM','corrected_transcript','TSMC 3Q26 earnings conference transcript','https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-10/eba4fac72873bf546b669bd2b5d973df5c93215d/TSMC%203Q26%20Transcript.pdf','2026-09-30','2026-10-15',None),
('TSM_2026_Q3_RELEASE','TSM','quarterly_update','TSMC 3Q26 earnings release','https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2026-10/526b65af7305df2dde6b9ece6812b1fdf1e49f9b/3Q26%20EarningsRelease.pdf','2026-09-30','2026-10-15','0001046179-26-000726'),
('MU_H200_2024','MU','partnership','Micron HBM3E volume production and NVIDIA H200 design','https://investors.micron.com/news/press-release/2024/Micron-Commences-Volume-Production-of-Industry-Leading-HBM3E-Solution-to-Accelerate-the-Growth-of-AI-02-26-2024/default.aspx',None,'2024-02-26',None),
('MU_H200_2024_PDF','MU','partnership_copy','Micron HBM3E announcement timestamped PDF','https://investors.micron.com/node/46656/pdf',None,'2024-02-26',None),
('TSM_3DFABRIC_ALLIANCE','TSM','partnership','TSMC OIP 3DFabric Alliance and Micron HBM compatibility','https://pr.tsmc.com/english/news/2968',None,'2022-10-27',None),
('TSM_COWOS','TSM','technology','TSMC CoWoS technology','https://3dfabric.tsmc.com/english/dedicatedFoundry/technology/cowos.htm',None,None,None),
('NVDA_BLACKWELL','NVDA','technology_announcement','NVIDIA Blackwell AI computing platform','https://nvidianews.nvidia.com/news/nvidia-blackwell-platform-arrives-to-power-a-new-era-of-computing/',None,'2024-03-18',None),
('NVDA_H200','NVDA','technology','NVIDIA H200 data center product','https://www.nvidia.com/en-us/data-center/h200/',None,None,None),
('MU_HBM3E','MU','technology','Micron HBM3E product','https://www.micron.com/products/memory/hbm/hbm3e',None,None,None),
('BIS_HBM_2024','POLICY','policy_announcement','BIS December 2024 semiconductor and HBM controls','https://www.bis.gov/press-release/commerce-strengthens-export-controls-restrict-chinas-capability-produce-advanced-semiconductors-military',None,'2024-12-02',None),
('FR_HBM_2024','POLICY','policy_original','Federal Register 2024-28270 HBM / advanced computing controls','https://www.govinfo.gov/content/pkg/FR-2024-12-05/pdf/2024-28270.pdf',None,'2024-12-05',None),
('BIS_H200_2026','POLICY','policy_announcement','BIS January 2026 H200 case-by-case licensing policy','https://media.bis.gov/sites/default/files/documents/DoC%20Revises%20License%20Review%20Policy%20for%20Semiconductors%20Exports.pdf',None,'2026-01-13',None),
('MU_HBM4_2026','MU','partnership','Micron volume HBM4 designed for NVIDIA Vera Rubin','https://investors.micron.com/news/press-release/2026/Micron-in-High-Volume-Production-of-HBM4-Designed-for-NVIDIA-Vera-Rubin-PCIe-Gen6-SSD-and-SOCAMM2-03-16-2026/default.aspx',None,'2026-03-16',None),
('FR_H200_2026','POLICY','policy_original','Final rule 2026-00789 revision to advanced computing license review','https://www.govinfo.gov/content/pkg/FR-2026-01-15/pdf/2026-00789.pdf',None,'2026-01-15',None),
]

def extract(source,path,content):
 blocks=[]
 if content.startswith(b'%PDF'):
  doc=fitz.open(path)
  for i,page in enumerate(doc):
   # Preserve each physical page and its complete table wording, not inferred cells.
   text=page.get_text(sort=True)
   blocks.append({'source_id':source['source_id'],'pdf_page':i+1,'paragraph':None,'text':text,
    'section_hint':'PDF page; chapter refined during reviewed evidence selection',
    'table_policy':'Keep page/heading/unit in context; no automatic cross-page numerical interpretation'})
  source['page_count']=len(doc)
 else:
  soup=BeautifulSoup(content,'html.parser')
  for tag in soup(['script','style','nav','footer','header']):tag.decompose()
  # Some issuer pages have a small unrelated <article> card before the release.
  # Prefer the full main region, and retain the full body when no main exists.
  root=soup.find('main') or soup
  section=''
  for element in root.find_all(['h1','h2','h3','h4','p','li','table']):
   if element.name!='table' and element.find_parent('table'):continue
   text=element.get_text(' ',strip=True)
   if not text:continue
   if element.name.startswith('h'):section=text
   blocks.append({'source_id':source['source_id'],'pdf_page':None,'paragraph':len(blocks)+1,
    'text':text,'section_hint':section,'is_table':element.name=='table',
    'table_policy':'HTML table preserved as one block with headers; raw HTML retains rows/columns'})
 jsonl(ROOT/'text'/f'{source["source_id"]}.jsonl',blocks)
 source['text_path']=f'text/{source["source_id"]}.jsonl';source['blocks']=len(blocks)

def main():
 sec={r['accessionNumber']:r for r in load(ROOT/'output/sec_sources.json')}
 previous={r['source_id']:r for r in load(ROOT/'output/sources.json')} if (ROOT/'output/sources.json').exists() else {}
 catalog=[]
 for sid,ticker,kind,title,url,period,pub,acc in SEEDS:
  if sid in previous and previous[sid].get('url')==url:
   catalog.append(previous[sid]);continue
  source={'source_id':sid,'ticker':ticker,'kind':kind,'title':title,'url':url,
   'publisher':{'MU':'Micron','NVDA':'NVIDIA','TSM':'TSMC','POLICY':'U.S. Government'}[ticker],
   'report_period':period,'published_date':pub,'published_at':None,'event_date':pub,
   'accession':acc,'retrieved_at':now(),'revision_of':None,'version_group':sid,
   'evidence_class':'original_disclosure','historical_version_verified':False}
  # Issuer annual-report bundles include a later proxy/letter; do not date the whole bundle at 10-K acceptance.
  if acc in sec and kind=='annual':
   source.update(published_at=sec[acc]['acceptanceDateTime'],sec_original_url=sec[acc]['url'],
    historical_version_verified=True,publication_basis='SEC acceptance metadata; issuer accession/form/period-matched copy; first-global publication unverified')
  elif pub:
   source['publication_basis']='issuer stated date; date-only conservative next-day UTC policy; mutable/current copy not historical verified'
  else:source['publication_basis']='undated mutable technical page; available only from retrieval timestamp'
  suffix='.pdf' if ('.pdf' in url.lower() or sid in ['MU_2026_Q3','MU_H200_2024_PDF']) else '.html'
  path=ROOT/'raw'/(sid+suffix)
  try:
   if path.exists() and previous.get(sid,{}).get('url',url)==url:content=path.read_bytes();status=200
   else:
    response=requests.get(url,headers={'User-Agent':'LocalFinancialRAG/1.0'},timeout=60)
    status=response.status_code;content=response.content
    if status==200:path.write_bytes(content)
   source['http_status']=status
   if status==200:
    source['local_path']=str(path.relative_to(ROOT)).replace('\\','/')
    source['sha256']=hashlib.sha256(content).hexdigest()
    extract(source,path,content)
    source['access_status']='archived_original_publisher_copy'
   else:source['access_status']='link_only'
  except Exception as e:source.update(access_status='error',error_type=type(e).__name__)
  catalog.append(source);dump(ROOT/'output/sources.json',catalog)
  print(json.dumps({'source':sid,'status':source['access_status'],'blocks':source.get('blocks'),'bytes':len(content) if 'content' in locals() else 0}),flush=True)
  time.sleep(.2)
 dump(ROOT/'output/sources.json',catalog)

if __name__=='__main__':main()
