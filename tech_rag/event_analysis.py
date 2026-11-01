"""Point-in-time expectation comparison and daily observations, never pricing proof."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import sqlite3
from common import ROOT,now
from monitor_schedule import utc_datetime,_calendar,_sessions

MATCH_FIELDS=('ticker','metric','fiscal_period','business_scope','unit','currency','accounting_basis','value_kind')

def market_sessions(calendar,around):
    frame=calendar.schedule.loc[(around-timedelta(days=70)).date().isoformat():(around+timedelta(days=370)).date().isoformat()]
    opened='open' if 'open' in frame.columns else 'market_open';closed='close' if 'close' in frame.columns else 'market_close'
    return [(utc_datetime(row[opened].to_pydatetime()),utc_datetime(row[closed].to_pydatetime())) for _,row in frame.iterrows()]

def comparable_gap(event,baselines,as_of):
    result=dict(status='unknown',difference_vs_issuer_guidance=[],difference_vs_named_forecast=[],
                difference_vs_market_consensus='unknown',priced_in='unknown',missing_requirements=[])
    anchor=event.get('first_public_at')
    if not anchor or event.get('first_public_status') not in ('verified','reviewed_earliest_known'):
        result['missing_requirements'].append('Verified earliest public anchor missing');return result
    first=utc_datetime(anchor);cutoff=utc_datetime(as_of);valid=[]
    for baseline in baselines:
        try:
            if baseline.get('review_status')!='reviewed' or not baseline.get('reviewed_by'):continue
            if not all(event.get(k) is not None and baseline.get(k)==event.get(k) for k in MATCH_FIELDS):continue
            if baseline.get('event_id')!=event.get('event_id'):continue
            if not baseline.get('version_id') or not baseline.get('url') or not baseline.get('quote'):continue
            published=utc_datetime(baseline.get('publication_latest',baseline['published_at']))
            if not published<first<=cutoff or not published<=utc_datetime(baseline['acquired_at'])<=cutoff:continue
            if utc_datetime(baseline['reviewed_at'])>cutoff:continue
            if baseline.get('valid_until') and utc_datetime(baseline['valid_until'])<first:continue
            if baseline.get('baseline_kind') not in ('issuer_guidance','named_forecast'):continue
            if not baseline.get('forecaster'):continue
            if baseline.get('withdrawn'):
                valid.append((published,baseline,None,None,None));continue
            low=baseline.get('low',baseline.get('value'));high=baseline.get('high',baseline.get('value'))
            value=event.get('value')
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (low,high,value)) or low>high:continue
            valid.append((published,baseline,low,high,value))
        except (ValueError,TypeError,KeyError):continue
    # Latest still-valid version per named forecaster and baseline type.
    selected={}
    for row in sorted(valid,key=lambda r:r[0]):selected[(row[1]['baseline_kind'],row[1]['forecaster'])]=row
    for _,baseline,low,high,value in selected.values():
        if baseline.get('withdrawn'):continue
        key='difference_vs_issuer_guidance' if baseline['baseline_kind']=='issuer_guidance' else 'difference_vs_named_forecast'
        result[key].append(dict(version_id=baseline['version_id'],forecaster=baseline['forecaster'],prior_low=low,prior_high=high,
            event_value=value,position='below_range' if value<low else ('above_range' if value>high else 'within_range'),
            difference_from_low=value-low,difference_from_high=value-high,unit=event['unit'],
            interpretation='Comparison with this specific pre-event baseline only; not market consensus'))
    if result['difference_vs_issuer_guidance'] or result['difference_vs_named_forecast']:result['status']='comparable_difference'
    else:result['missing_requirements'].append('No reviewed, time-eligible, same-scope pre-event baseline')
    return result

def event_windows(first_public_at,as_of,days=(0,1,5),market='XNAS'):
    anchor=utc_datetime(first_public_at);cutoff=utc_datetime(as_of)
    if anchor>cutoff:raise ValueError('Event is not yet public at judgment time')
    calendar,metadata=_calendar({'market':market},anchor)
    sessions=market_sessions(calendar,anchor)
    # At the exact close, conservatively start with the next session.
    index=next(i for i,(_,close) in enumerate(sessions) if close>anchor)
    if index<1:raise ValueError('Prior completed session unavailable')
    base=sessions[index-1][1]
    t0_open,t0_close=sessions[index]
    mixed=t0_open<=anchor<t0_close
    windows=[]
    for day in days:
        if type(day) is not int or not 0<=day<=252:raise ValueError('Invalid trading-day offset')
        if index+day>=len(sessions):raise ValueError('Observation exceeds available exchange-calendar horizon')
        end=sessions[index+day][1]
        windows.append(dict(trading_day_offset=day,start=base.isoformat(),end=end.isoformat(),
            start_session=base.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
            end_session=end.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
            status='complete' if end<=cutoff else 'window_incomplete',mixed_pre_and_post_event=mixed))
    previous=[close.astimezone(ZoneInfo('America/New_York')).date().isoformat() for _,close in sessions[:index]][-20:]
    return dict(windows=windows,calendar=metadata,t0=t0_close.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
        pre_event_volume_sessions=previous,intraday_response='unknown',causal_attribution='unknown',priced_in='unknown')

class YahooDaily:
    def __init__(self,directory=None):
        self.directory=Path(directory or ROOT/'state/event_market');self.directory.mkdir(parents=True,exist_ok=True)

    def fetch(self,ticker,start,end,*,before_request=None):
        """Explicit unadjusted OHLCV plus vendor-adjusted close, no forward filling."""
        import yfinance as yf
        from curl_cffi import requests
        from integration.industry_news import _SEARCH_LOCK
        class Session(requests.Session):
            def request(self,*args,**kwargs):
                if before_request:before_request()
                return super().request(*args,**kwargs)
        day=utc_datetime().date().isoformat()
        key=hashlib.sha256(json.dumps([ticker,start,end,day]).encode()).hexdigest();path=self.directory/(key+'.json')
        if path.exists():
            saved=json.loads(path.read_text(encoding='utf-8'))
            # Do not cache an incomplete trading day for the whole day.
            if utc_datetime()-utc_datetime(saved['acquired_at'])<timedelta(minutes=15):return saved
        with _SEARCH_LOCK:
            with Session(impersonate='chrome') as session:
                frame=yf.Ticker(ticker,session=session).history(start=start,end=end,interval='1d',auto_adjust=False,actions=True,raise_errors=True)
        if frame.empty:raise ValueError('No daily market data')
        def number(value):return float(value) if value is not None and math.isfinite(float(value)) else None
        bars=[dict(date=stamp.date().isoformat(),close=number(row.get('Close')),adjusted_close=number(row.get('Adj Close')),
                   volume=number(row.get('Volume')),split=number(row.get('Stock Splits',0)),dividend=number(row.get('Dividends',0)))
              for stamp,row in frame.iterrows()]
        result=dict(provider='Yahoo Finance via yfinance',ticker=ticker,acquired_at=now(),bars=bars,
                    adjustment='vendor adjusted close, including corporate actions; raw volume retained')
        result['raw_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');temp.replace(path)
        return result

class DailyEventMarket:
    accounted_transport=True
    def __init__(self,provider=None):self.provider=provider or YahooDaily()

    def __call__(self,request,*,as_of='current',before_request=None):
        result=dict(status='unknown',windows=[],priced_in='unknown',causal_attribution='unknown',intraday_response='unknown',unknowns=[])
        if not request.get('anchor') or request.get('anchor_status') not in ('verified','reviewed_earliest_known'):
            result['unknowns'].append('First public time is not verified; article/discovery times cannot substitute');return result
        live=as_of=='current';cutoff=utc_datetime() if live else utc_datetime(as_of)
        mapped=event_windows(request['anchor'],cutoff,request.get('windows',[0,1,5]),request.get('market','XNAS'))
        completed=[w for w in mapped['windows'] if w['status']=='complete']
        if not completed:return {**result,**mapped,'status':'window_incomplete'}
        start=(utc_datetime(request['anchor'])-timedelta(days=60)).date().isoformat()
        end=(cutoff+timedelta(days=1)).date().isoformat()
        try:stock=self.provider.fetch(request['ticker'],start,end,before_request=before_request)
        except Exception as exc:
            if type(exc).__name__ in ('DailyBudgetStop','PlanInactive'):raise
            return {**result,**mapped,'unknowns':['Stock source failed: '+type(exc).__name__]}
        try:benchmark=self.provider.fetch(request.get('benchmark','SPY'),start,end,before_request=before_request)
        except Exception as exc:
            if type(exc).__name__ in ('DailyBudgetStop','PlanInactive'):raise
            benchmark=dict(bars=[],acquired_at=now(),provider='unavailable')
        if live:cutoff=utc_datetime()
        if not live and any(utc_datetime(v['acquired_at'])>cutoff for v in (stock,benchmark) if v.get('provider')!='unavailable'):
            return {**result,**mapped,'unknowns':['Snapshots acquired after historical judgment; replay forbidden']}
        calendar,_=_calendar({'market':request.get('market','XNAS')},cutoff)
        # Filter by actual exchange close, including early closes, before arithmetic.
        closes={close.astimezone(ZoneInfo('America/New_York')).date().isoformat():close for _,close in market_sessions(calendar,utc_datetime(request['anchor']))}
        def eligible(snapshot):
            return {b['date']:b for b in snapshot['bars'] if b['date'] in closes and closes[b['date']]<=cutoff}
        bars=eligible(stock);bench=eligible(benchmark);out=[]
        def positive(v):return type(v) in (float,int) and math.isfinite(v) and v>0
        for window in mapped['windows']:
            row={**window,'return_pct':None,'relative_return_pct':None,'volume_ratio':None}
            a=bars.get(window['start_session']);b=bars.get(window['end_session'])
            if window['status']=='complete' and a and b and positive(a.get('adjusted_close')) and positive(b.get('adjusted_close')):
                row.update(status='daily_observation_available',return_pct=(b['adjusted_close']/a['adjusted_close']-1)*100)
                ba=bench.get(window['start_session']);bb=bench.get(window['end_session'])
                if ba and bb and positive(ba.get('adjusted_close')) and positive(bb.get('adjusted_close')):
                    row['relative_return_pct']=row['return_pct']-(bb['adjusted_close']/ba['adjusted_close']-1)*100
                volume_rows=[bars.get(day) for day in mapped['pre_event_volume_sessions']]
                if len(volume_rows)==20 and all(v and positive(v.get('volume')) and not v.get('split') for v in volume_rows) and positive(b.get('volume')) and not any(v.get('split') for day,v in bars.items() if window['start_session']<=day<=window['end_session']):
                    row['volume_ratio']=b['volume']/(sum(v['volume'] for v in volume_rows)/20)
            elif window['status']=='complete':row['status']='unknown_missing_endpoint'
            out.append(row)
        return {**result,**mapped,'windows':out,'status':'daily_observation_available' if any(w['status']=='daily_observation_available' for w in out) else 'unknown',
                'analysis_time':cutoff.isoformat(),'snapshots':[{k:v.get(k) for k in ('provider','ticker','acquired_at','raw_sha256','adjustment')} for v in (stock,benchmark)],
                'benchmark':request.get('benchmark','SPY'),'interpretation':'Observed daily changes; not proof of event causation or degree priced in'}

class EventEvidence:
    """Append-only user-reviewed event facts/baselines linked to archived sources."""
    def __init__(self,path=None):
        self.path=Path(path or ROOT/'state/event_evidence.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS evidence(event_id TEXT,version INTEGER,payload TEXT,PRIMARY KEY(event_id,version))')

    def append(self,event_id,payload,*,source_resolver):
        if not payload.get('reviewed_by'):raise ValueError('Explicit human reviewer is required')
        allowed=set(MATCH_FIELDS)|{'reviewed_by','first_public_at','baselines','event_facts','value','note','novelty_review'}
        if set(payload)-allowed:raise ValueError('Unknown event evidence fields')
        if not isinstance(payload.get('baselines',[]),list) or not isinstance(payload.get('event_facts',[]),list):raise ValueError('Evidence arrays required')
        if len(payload.get('baselines',[]))+len(payload.get('event_facts',[]))>50:raise ValueError('Evidence batch too large')
        # Every quoted source must resolve through the backend, never caller-provided text alone.
        for item in payload.get('baselines',[])+payload.get('event_facts',[]):
            source=source_resolver(item['version_id'])
            if not source or not item.get('quote') or item['quote'] not in source.get('text',''):
                raise ValueError('Evidence quote not in trusted archived source')
            if item.get('url')!=source.get('url'):raise ValueError('Evidence URL differs from archived source')
            for key in ('acquired_at','published_at'):
                original=source.get(key);given=item.get(key)
                if (original is None)!=(given is None) or (original is not None and utc_datetime(original)!=utc_datetime(given)):
                    raise ValueError('Source clocks must match backend archive')
        if payload.get('first_public_at'):
            if not payload.get('event_facts') or not any(i.get('published_at') and utc_datetime(i['published_at'])==utc_datetime(payload['first_public_at']) for i in payload['event_facts']):
                raise ValueError('Public anchor must match a reviewed original source')
        novelty=payload.get('novelty_review')
        if novelty:
            if novelty.get('status') not in ('new_fact','no_new_fact','unknown') or not novelty.get('reason'):
                raise ValueError('Novelty review needs a status and explicit comparison reason')
            if novelty['status']!='unknown' and not payload.get('event_facts'):raise ValueError('Novelty requires archived event evidence')
            for version_id in novelty.get('prior_version_ids',[]):
                original=source_resolver(version_id)
                if not original or not original.get('published_at') or not payload.get('first_public_at') or utc_datetime(original['published_at'])>=utc_datetime(payload['first_public_at']):
                    raise ValueError('Prior reports must be archived and public before the event')
            if novelty['status']=='no_new_fact' and not novelty.get('prior_version_ids'):raise ValueError('Old-event classification requires earlier source versions')
        row={**payload,'event_id':event_id,'reviewed_at':now()}
        row['first_public_status']='reviewed_earliest_known' if payload.get('first_public_at') else 'unknown'
        for item in row.get('baselines',[]):
            item.update(event_id=event_id,reviewed_at=row['reviewed_at'],reviewed_by=row['reviewed_by'],review_status='reviewed')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM evidence WHERE event_id=?',(event_id,)).fetchone()[0]
            db.execute('INSERT INTO evidence VALUES (?,?,?)',(event_id,version,json.dumps(row,ensure_ascii=False)))
        return dict(version=version,event_id=event_id)

    def latest(self,event_id,as_of=None):
        cutoff=utc_datetime(as_of) if as_of else utc_datetime()
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT version,payload FROM evidence WHERE event_id=? ORDER BY version DESC',(event_id,)).fetchall()
        for version,payload in rows:
            value=json.loads(payload)
            if utc_datetime(value['reviewed_at'])<=cutoff:return {**value,'version':version}
        return {}
