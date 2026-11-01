"""Append-only research loop. Lessons are model context, never source facts."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,math,re,sqlite3,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,parse_time

def utc(value):return parse_time(value).astimezone(timezone.utc)
def now():return datetime.now(timezone.utc).isoformat()
def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()

class ResearchMemory:
    def __init__(self,path=None):
        self.path=Path(path or ROOT/'state/us_research/memory.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS conclusions (id TEXT PRIMARY KEY, run_id TEXT, ticker TEXT, arm TEXT, namespace TEXT, judged_at TEXT, recorded_at TEXT, payload TEXT, UNIQUE(namespace,run_id,arm))')
            db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, conclusion_id TEXT, checked_at TEXT, recorded_at TEXT, payload TEXT)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def record(self,run_id,ticker,arm,judged_at,conclusion,*,namespace='production',recorded_at=None):
        recorded=utc(recorded_at or now()).isoformat();judged=utc(judged_at).isoformat()
        if utc(recorded)<utc(judged):raise ValueError('conclusion cannot be recorded before judgment')
        if arm not in ('A','B') or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',ticker):raise ValueError('invalid ticker/arm')
        if not isinstance(conclusion,dict) or not conclusion.get('final_conclusion'):raise ValueError('complete final conclusion required')
        payload=encode(conclusion);key='RM_'+digest([namespace,run_id,ticker,arm,judged,conclusion])[:28]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id FROM conclusions WHERE namespace=? AND run_id=? AND arm=?',(namespace,run_id,arm)).fetchone()
            if previous:
                if previous['id']!=key:raise ValueError('immutable conclusion conflict; new run/version required')
                return key
            db.execute('INSERT INTO conclusions VALUES (?,?,?,?,?,?,?,?)',(key,run_id,ticker,arm,namespace,judged,recorded,payload))
        return key
    def get(self,key):
        with self.db() as db:row=db.execute('SELECT * FROM conclusions WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('memory conclusion not found')
        return dict(row)
    def conclusions(self,namespace=None):
        with self.db() as db:
            rows=db.execute('SELECT * FROM conclusions'+(' WHERE namespace=?' if namespace else ''),(namespace,) if namespace else ()).fetchall()
        return [dict(x) for x in rows]
    def review(self,key,checked_at,observations,lesson,*,recorded_at=None,window=None,metrics=None):
        row=self.get(key);checked=utc(checked_at);recorded=utc(recorded_at or now())
        if checked<utc(row['judged_at']) or recorded<checked:raise ValueError('invalid review clocks')
        if not isinstance(observations,list) or not observations or not isinstance(lesson,str) or not lesson.strip():raise ValueError('sourced observations and lesson required')
        from integration.news_body import safe_url
        for item in observations:
            safe_url(item.get('url'))
            public=utc(item.get('published_at',''));acquired=utc(item.get('acquired_at',''))
            if public>acquired or acquired>checked:raise ValueError('observation was not publicly available and acquired by review')
        payload=dict(observations=observations,lesson=lesson.strip(),window=window,metrics=metrics,
            fact_promotion_allowed=False,causal_attribution='unknown',kind='research_followup_not_verified_business_fact')
        identity='RV_'+digest([key,checked.isoformat(),payload])[:28]
        with self.db() as db:db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?)',(identity,key,checked.isoformat(),recorded.isoformat(),encode(payload)))
        return identity
    def reviews(self,key):
        with self.db() as db:rows=db.execute('SELECT * FROM reviews WHERE conclusion_id=? ORDER BY checked_at,recorded_at',(key,)).fetchall()
        return [{**dict(x),'payload':json.loads(x['payload'])} for x in rows]
    def context(self,ticker,as_of,*,namespace='production',arm='B',limit=5):
        at=utc(as_of);entries=[]
        for row in self.conclusions(namespace):
            if row['ticker']!=ticker or row['arm']!=arm or utc(row['judged_at'])>=at or utc(row['recorded_at'])>at:continue
            reviews=[r for r in self.reviews(row['id']) if utc(r['checked_at'])<=at and utc(r['recorded_at'])<=at]
            if not reviews:continue
            last=reviews[-1]
            entries.append(dict(memory_id=row['id'],review_id=last['id'],judged_at=row['judged_at'],
                checked_at=last['checked_at'],recorded_at=last['recorded_at'],**last['payload']))
        return sorted(entries,key=lambda x:(x['checked_at'],x['recorded_at']),reverse=True)[:limit]

def followup_windows(judged_at,days=(1,5)):
    import exchange_calendars as xc
    cal=xc.get_calendar('XNYS');local=utc(judged_at).astimezone(ZoneInfo('America/New_York'))
    # Conservative entry: first session strictly after judgment's NY date.
    entry=cal.date_to_session((local.date()+timedelta(days=1)).isoformat(),direction='next')
    return [dict(days=n,entry_date=str(entry.date()),exit_date=str(cal.session_offset(entry,n).date()),
                 due_at=cal.session_close(cal.session_offset(entry,n)).isoformat()) for n in days]

def yahoo_window(ticker,window,as_of):
    """Actual daily price observations; no historical retrieval-time claims."""
    import yfinance as yf
    # A live fetch finishes after it starts. Only explicit historical/replay
    # cutoffs constrain acquisition; never freeze "now" before a live fetch.
    if as_of and utc(now())>utc(as_of):return None
    end=(datetime.fromisoformat(window['exit_date'])+timedelta(days=1)).date().isoformat()
    rows=[]
    for symbol in (ticker,'SPY'):
        frame=yf.Ticker(symbol).history(start=window['entry_date'],end=end,auto_adjust=True,actions=False)
        if frame.empty:return None
        indexed={str(idx.date()):float(r['Close']) for idx,r in frame.iterrows()}
        if any(d not in indexed for d in (window['entry_date'],window['exit_date'])):return None
        a,b=indexed[window['entry_date']],indexed[window['exit_date']]
        if not all(math.isfinite(v) and v>0 for v in (a,b)):return None
        rows.append((b/a-1)*100)
    acquired=now()
    if as_of and utc(acquired)>utc(as_of):return None
    return dict(raw_return_pct=rows[0],benchmark_return_pct=rows[1],relative_return_pct=rows[0]-rows[1],
        acquired_at=acquired,published_at=window['due_at'],url=f'https://finance.yahoo.com/quote/{ticker}/history/')

def settle_memory(memory,as_of=None,*,market_provider=None,namespace='production'):
    at=utc(as_of or now());provider=market_provider or yahoo_window;completed=0;pending=[];errors=[];cache={}
    # Separate settlement lock prevents concurrent duplicate network requests.
    lock=memory.path.with_suffix('.settling')
    try:fd=lock.open('x')
    except FileExistsError:return dict(completed=0,status='already_running',pending=[],errors=[])
    try:
        for row in memory.conclusions(namespace):
            if utc(row['recorded_at'])>at:continue
            done={r['payload'].get('window',{}).get('days') for r in memory.reviews(row['id']) if r['payload'].get('window')}
            for window in followup_windows(row['judged_at']):
                if window['days'] in done:continue
                if utc(window['due_at'])>at:
                    pending.append(dict(memory_id=row['id'],window=window,reason='window_not_complete'));continue
                try:
                    # For live collection allow its actual acquisition clock;
                    # explicit historical as_of cannot acquire a past vintage.
                    cutoff=at.isoformat() if as_of else None
                    cache_key=(row['ticker'],window['entry_date'],window['exit_date'])
                    if cache_key not in cache:cache[cache_key]=provider(row['ticker'],window,cutoff)
                    metrics=cache[cache_key]
                    if not metrics:
                        pending.append(dict(memory_id=row['id'],window=window,reason='market_data_unknown'));continue
                    vals=[metrics.get(k) for k in ('raw_return_pct','benchmark_return_pct','relative_return_pct')]
                    if not all(type(v) in (int,float) and math.isfinite(v) for v in vals):raise ValueError('invalid market metrics')
                    checked=metrics['acquired_at']
                    if cutoff and utc(checked)>utc(cutoff):raise ValueError('future acquisition')
                    lesson=(f"{window['days']}个交易日窗口内观察到相对SPY表现{metrics['relative_return_pct']:+.3f}%。"
                        '这只是价格路径，不能证明新闻因果、预期差或长期经营判断正确；继续核实原结论中的经营条件。')
                    memory.review(row['id'],checked,[{k:metrics[k] for k in ('url','published_at','acquired_at')}],lesson,
                        window=window,metrics=metrics)
                    completed+=1
                except Exception as exc:errors.append(dict(memory_id=row['id'],window=window,error_type=type(exc).__name__))
    finally:fd.close();lock.unlink()
    return dict(completed=completed,status='checked',pending=pending,errors=errors)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['context','settle','review','record-result']);p.add_argument('--ticker',default='NVDA')
    p.add_argument('--as-of');p.add_argument('--namespace',default='production');p.add_argument('--arm',default='B');p.add_argument('--file',type=Path)
    args=p.parse_args();m=ResearchMemory()
    if args.action=='context':out=m.context(args.ticker,args.as_of or now(),namespace=args.namespace,arm=args.arm)
    elif args.action=='settle':out=settle_memory(m,args.as_of,namespace=args.namespace)
    else:
        if not args.file:p.error('--file required')
        data=json.loads(args.file.read_text(encoding='utf-8'))
        if args.action=='review':out={'review_id':m.review(data['memory_id'],data['checked_at'],data['observations'],data['lesson'])}
        else:out={'memory_id':m.record(data['run_id'],data['ticker'],data.get('arm','B'),data['analysis_time'],data['final'],namespace=args.namespace)}
    print(encode(out))
