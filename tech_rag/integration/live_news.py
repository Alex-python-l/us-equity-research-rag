"""Bounded current Yahoo/yfinance discovery. Metadata never becomes evidence."""
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit
from common import parse_time,sha
from aliases import resolve_company

def _fetch_yahoo(ticker,count):
 import yfinance as yf
 # Reuse the installed SDK; no API key, paid service or automatic retry.
 return yf.Search(ticker,news_count=count,max_results=0,lists_count=0,
                  timeout=10,raise_errors=True,enable_fuzzy_query=False).news or []

def search_live_news(ticker,analysis_time,historical,lookback_days=7,limit=5):
 at=parse_time(analysis_time);symbol=resolve_company(ticker)['research_symbol']
 base={'tool':'search_news','ticker':symbol,'analysis_time':at.isoformat(),'items':[],
       'provider':'Yahoo Finance via installed yfinance SDK','body_acquired':False,
       'source_verified':False,'historical_eligible':False,'paid_api_used':False,
       'coverage':'Recent relevance-ranked provider sample, not exhaustive news coverage.',
       'gap':'Titles, provider summaries and links are unverified candidates; retrieve and verify the original body before making facts.'}
 if historical:return {**base,'status':'unsupported_historical','reason':'No point-in-time news version provider; live news disabled for historical research.'}
 if type(lookback_days) is not int or not 1<=lookback_days<=30 or type(limit) is not int or not 1<=limit<=10:
  raise ValueError('lookback_days must be 1..30 and limit 1..10')
 seen_at=datetime.now(timezone.utc).isoformat();cutoff=at-timedelta(days=lookback_days)
 try:articles=_fetch_yahoo(symbol,min(20,limit*2))
 except Exception as exc:return {**base,'status':'provider_unavailable','error_type':type(exc).__name__,'fetched_at':seen_at}
 items=[];seen=set();excluded=0
 for article in articles:
  content=article.get('content') if isinstance(article.get('content'),dict) else article
  stamp=content.get('pubDate') or content.get('pub_date')
  if not stamp and content.get('providerPublishTime') is not None:
   try:stamp=datetime.fromtimestamp(content['providerPublishTime'],timezone.utc).isoformat()
   except (ValueError,TypeError,OverflowError,OSError):stamp=None
  try:published=parse_time(stamp)
  except (ValueError,TypeError,AttributeError):excluded+=1;continue
  if not cutoff<=published<=at:excluded+=1;continue
  url=content.get('canonicalUrl') or content.get('clickThroughUrl') or content.get('link') or ''
  if isinstance(url,dict):url=url.get('url','')
  parsed=urlsplit(url) if isinstance(url,str) else None
  if not parsed or parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:excluded+=1;continue
  title=str(content.get('title') or '')[:400]
  if not title.strip():excluded+=1;continue
  identity=(url,published.isoformat())
  if identity in seen:continue
  seen.add(identity)
  provider=content.get('provider') or {}
  publisher=provider.get('displayName') if isinstance(provider,dict) else None
  items.append({'candidate_id':'NC_'+sha(url+'|'+published.isoformat())[:24],
   'title':title,'summary':str(content.get('summary') or '')[:700],
   'url':url,'publisher':publisher or content.get('publisher') or 'Unknown',
   'provider_published_at':published.isoformat(),'actual_public_at':None,'first_seen_at':seen_at,
   'status':'candidate_metadata','body_acquired':False,'source_verified':False,'historical_eligible':False})
 items.sort(key=lambda x:(x['provider_published_at'],x['candidate_id']),reverse=True)
 return {**base,'status':'candidates' if items else 'no_eligible_candidates','items':items[:limit],
         'fetched_at':seen_at,'lookback_days':lookback_days,'excluded_records':excluded,'returned':min(len(items),limit),
         'sample_truncated':len(items)>limit}
