"""Independent official-news/event discovery; new candidates are not auto-promoted to facts."""
from common import *
from datetime import timedelta
from urllib.parse import urlparse
import requests,re

CIKS={'MU':723125,'NVDA':1045810,'TSM':1046179}

def news_search(ticker,as_of,query='',lookback_days=120,refresh=True,limit=8):
 if ticker not in CIKS:raise ValueError('unsupported core ticker')
 if not 1<=limit<=30 or not 1<=lookback_days<=730:raise ValueError('invalid news bounds')
 at=parse_time(now()) if as_of=='current' else parse_time(as_of)
 cutoff=at-timedelta(days=lookback_days)
 historical=as_of!='current';items=[];issues=[]
 for s in load(ROOT/'output/source_catalog.json'):
  if s['ticker'] not in [ticker,'POLICY'] or s['kind'] not in ['partnership','quarterly_update','technology_announcement','policy_original','policy_announcement','corrected_transcript']:continue
  if not s.get('available_at'):continue
  stamp=parse_time(s['available_at'])
  if not cutoff<=stamp<=at:continue
  if historical and not s.get('historical_eligible'):
   issues.append({'source_id':s['source_id'],'reason':'historical_content_version_unverified'});continue
  items.append({'source_id':s['source_id'],'title':s['title'],'url':s['url'],
   'published_date':s.get('published_date'),'published_at':s.get('published_at'),
   'available_at':s['available_at'],'kind':s['kind'],'status':'archived_official_source',
   'historical_eligible':s['historical_eligible'],'may_update_graph_automatically':False})
 live_status='disabled_for_historical_analysis' if historical else 'not_requested'
 live_eligible=0;live_added=0;live_limit=6
 # Fetch one independent primary event feed for current dynamic questions, bounded and cached per invocation.
 if refresh and not historical:
  u=f'https://data.sec.gov/submissions/CIK{CIKS[ticker]:010d}.json'
  try:
   r=requests.get(u,headers={'User-Agent':'LocalFinancialRAG/1.0 research documentation'},timeout=25)
   r.raise_for_status();data=r.json();raw=r.content
   run_id=sha(ticker+now())[:16];path=ROOT/'raw'/f'news_feed_{ticker}_{run_id}.json';path.write_bytes(raw)
   recent=data['filings']['recent'];seen={item['accession'] for item in items if item.get('accession')}
   for i,form in enumerate(recent['form']):
    if form not in ['8-K','6-K']:continue
    accepted=recent['acceptanceDateTime'][i];filed=recent['filingDate'][i]
    available=(parse_time(filed+'T00:00:00+00:00')+timedelta(hours=36)).isoformat()
    if not cutoff<=parse_time(available)<=at:continue
    accession=recent['accessionNumber'][i]
    if accession in seen:continue
    live_eligible+=1
    if live_added>=live_limit:continue
    doc=recent['primaryDocument'][i]
    items.append({'source_id':f'NEWS_{ticker}_{accession}','title':f'{ticker} {form}: {doc}',
     'url':f'https://www.sec.gov/Archives/edgar/data/{CIKS[ticker]}/{accession.replace("-","")}/{doc}',
     'accession':accession,'published_at':None,'published_date':filed,'actual_public_at':None,
     'sec_acceptance_api_raw':accepted,'available_at':available,
     'availability_basis':'filing date + 36 hours UTC; acceptance is raw metadata, not verified first publication',
     'kind':'sec_event_candidate',
     'status':'metadata_only_requires_document_read','historical_eligible':False,
     'may_update_graph_automatically':False,'feed_snapshot_sha256':__import__('hashlib').sha256(raw).hexdigest()})
    seen.add(accession);live_added+=1
   live_status='success_primary_SEC_event_metadata'
  except Exception as exc:
   live_status='failed';issues.append({'reason':'live_feed_unavailable','error_type':type(exc).__name__})
 items.sort(key=lambda x:x['available_at'],reverse=True)
 return {'tool':'independent_news_search','ticker':ticker,'query':query,'analysis_time':at.isoformat(),
  'lookback_days':lookback_days,'live_refresh_status':live_status,'items':items[:limit],
  'returned_candidate_pool':len(items),'total_candidates':len(items)+live_eligible-live_added,
  'truncated':len(items)>limit or live_eligible>live_limit,'issues':issues,
  'live_eligible_metadata_candidates':live_eligible,'live_metadata_limit':live_limit,
  'live_metadata_truncated':live_eligible>live_limit,
  'query_semantics':'context for discovery; no full-text keyword filtering of SEC metadata',
  'coverage':'official archived releases/policy + independent SEC event metadata, not full-market or full-text live news',
  'gap':'新候选只有元数据时，正文读取/核验前不能成为供应关系或订单事实。'}
