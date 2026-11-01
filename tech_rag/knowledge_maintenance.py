"""Bounded official-source collection and explicitly confirmed maintenance jobs."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit,urljoin
from common import ROOT,now,load,readl,parse_time
from knowledge_versions import KnowledgeVersions,active_snapshot,safe_path
from monitor_schedule import default_config,next_run,utc_datetime

CIKS={'NVDA':1045810,'MU':723125,'TSM':1046179}
IR_LISTINGS={'NVDA':'https://investor.nvidia.com/news-and-events/press-releases/default.aspx',
             'MU':'https://investors.micron.com/news-events/press-releases',
             'TSM':'https://pr.tsmc.com/english/news'}
FORMS={'10-K','10-Q','8-K','20-F','6-K','10-K/A','10-Q/A','8-K/A','20-F/A','6-K/A'}
DEFAULTS=dict(tickers=['NVDA','MU','TSM'],close_delay_minutes=90,audit_weekday=6,audit_time='09:00',
              display_timezone='Asia/Shanghai',initial_days=90,overlap_days=7,requests_per_run=60,requests_per_day=120)

def encoded(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(v):return hashlib.sha256(encoded(v).encode()).hexdigest()

class MaintenanceBudgetStop(RuntimeError):pass

def validate(config):
    c={**DEFAULTS,**config}
    if set(config)-set(DEFAULTS) or not c['tickers'] or set(c['tickers'])-set(CIKS) or len(c['tickers'])!=len(set(c['tickers'])):
        raise ValueError('Unsupported knowledge maintenance configuration')
    for name,lo,hi in [('close_delay_minutes',0,1440),('audit_weekday',0,6),('initial_days',1,3650),
                       ('overlap_days',0,365),('requests_per_run',1,10000),('requests_per_day',1,100000)]:
        if type(c[name]) is not int or not lo<=c[name]<=hi:raise ValueError('Invalid maintenance '+name)
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',c['audit_time']):raise ValueError('Audit time requires HH:MM')
    ZoneInfo(c['display_timezone'])
    return c

def next_times(config,after=None):
    c=validate(config);at=utc_datetime(after)
    schedule=default_config('NVDA',at)
    schedule['cadence'].update(close_delay_minutes=c['close_delay_minutes'],guide_weekday=c['audit_weekday'],
                              guide_time=c['audit_time'],guide_timezone=c['display_timezone'])
    out={}
    for kind,upstream in [('ingest','fundamental'),('audit','guide_maintenance')]:
        times=[];cursor=at
        for _ in range(3):
            value=next_run(schedule,upstream,cursor);times.append(value);cursor=utc_datetime(value)
        out[kind]=times
    return out

def extract_blocks(content,sid):
    blocks=[]
    if content.startswith(b'%PDF'):
        import fitz
        with fitz.open(stream=content,filetype='pdf') as doc:
            if len(doc)>1000:raise ValueError('Document exceeds page limit')
            for i,page in enumerate(doc):
                blocks.append(dict(source_id=sid,pdf_page=i+1,paragraph=None,text=page.get_text(sort=True),
                    table_policy='Keep physical page, headers and footnotes; no inferred table cells'))
    else:
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(content,'html.parser')
        for tag in soup(['script','style','nav','footer','header']):tag.decompose()
        main=soup.find('main') or soup;section=''
        for node in main.find_all(['h1','h2','h3','h4','p','li','table']):
            if node.name!='table' and node.find_parent('table'):continue
            text=node.get_text(' ',strip=True)
            if not text:continue
            if node.name.startswith('h'):section=text
            blocks.append(dict(source_id=sid,pdf_page=None,paragraph=len(blocks)+1,text=text,section_hint=section,
                table_policy='Original HTML table kept as one block with headers and footnotes'))
    if not blocks or sum(len(b['text']) for b in blocks)>4_000_000:raise ValueError('Missing or oversized extracted body')
    return blocks

class OfficialCollector:
    def __init__(self,root=ROOT,request=None):
        self.root=Path(root);self.state=self.root/'state/kb';self.state.mkdir(parents=True,exist_ok=True)
        self.request=request

    def collect(self,config,*,before_request=None,check_owner=None):
        from integration.source_config import configure_sources
        configure_sources();contact=os.environ.get('SEC_EDGAR_USER_AGENT','').strip()
        if not contact:raise ValueError('SEC_EDGAR_USER_AGENT not configured')
        c=validate(config);snapshot=active_snapshot(self.root);sources=load(snapshot.file('source_catalog.json'))
        allowed={'data.sec.gov','www.sec.gov'}|{urlsplit(s['url']).hostname for s in sources if s.get('url')}
        checkpoints=load(self.state/'checkpoints.json') if (self.state/'checkpoints.json').exists() else {}
        total=0;documents=[];errors=[];updates={};stamp=now();seen_urls=set()
        def get(url):
            nonlocal total
            parts=urlsplit(url)
            if parts.scheme!='https' or parts.hostname not in allowed or parts.username or parts.password or parts.port not in (None,443):
                raise ValueError('Unregistered official source host')
            if total>=c['requests_per_run']:raise MaintenanceBudgetStop('Maintenance request limit reached')
            if check_owner:check_owner()
            if before_request:before_request()
            total+=1
            if self.request:return self.request(url)
            import requests
            headers={'User-Agent':contact if parts.hostname in ('www.sec.gov','data.sec.gov') else 'US Equity Research Workspace'}
            with requests.get(url,headers=headers,timeout=(10,45),allow_redirects=False,stream=True) as response:
                response.raise_for_status()
                if response.status_code!=200:raise ValueError('Official response not a complete document')
                body=bytearray()
                for part in response.iter_content(65536):
                    body.extend(part)
                    if len(body)>30_000_000:raise ValueError('Official document too large')
            time.sleep(.25)
            return bytes(body)
        def archive(source,content):
            fingerprint=hashlib.sha256(content).hexdigest();sid=source['source_id']
            objects=self.state/'objects';objects.mkdir(parents=True,exist_ok=True)
            raw=objects/(fingerprint+('.pdf' if content.startswith(b'%PDF') else '.html'))
            blocks=extract_blocks(content,sid)
            text=objects/(fingerprint+'_'+hashlib.sha256(sid.encode()).hexdigest()[:12]+'.jsonl')
            if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest()!=fingerprint:raise ValueError('Archived object corrupted')
            if not raw.exists():
                with raw.open('xb') as f:f.write(content)
            expected=''.join(json.dumps(b,ensure_ascii=False)+'\n' for b in blocks)
            if text.exists() and text.read_text(encoding='utf-8')!=expected:raise ValueError('Extracted object changed')
            if not text.exists():
                with text.open('x',encoding='utf-8') as f:f.write(expected)
            source.update(sha256=fingerprint,local_path=raw.relative_to(self.root).as_posix(),
                          text_path=text.relative_to(self.root).as_posix(),access_status='archived_original',retrieved_at=stamp)
            documents.append((source,blocks))
        for ticker in c['tickers']:
            try:
                data=json.loads(get(f'https://data.sec.gov/submissions/CIK{CIKS[ticker]:010d}.json'))
                recent=data['filings']['recent'];floor=utc_datetime(checkpoints.get(ticker,stamp))-timedelta(days=c['overlap_days'] if ticker in checkpoints else c['initial_days'])
                rows=[{key:recent[key][i] for key in ('form','acceptanceDateTime','accessionNumber','primaryDocument','reportDate')} for i in range(len(recent['form']))]
                for row in sorted(rows,key=lambda r:r['acceptanceDateTime']):
                    accepted=utc_datetime(row['acceptanceDateTime'])
                    if row['form'] not in FORMS or accepted<floor or accepted>utc_datetime(stamp):continue
                    if any(s.get('accession')==row['accessionNumber'] for s in sources):continue
                    acc=row['accessionNumber'];url=f'https://www.sec.gov/Archives/edgar/data/{CIKS[ticker]}/{acc.replace("-","")}/{row["primaryDocument"]}'
                    archive(dict(source_id=ticker+'_'+acc,ticker=ticker,kind='sec_filing',accession=acc,title=ticker+' '+row['form'],
                        url=url,publisher='SEC / issuer',report_period=row['reportDate'],published_at=accepted.isoformat(),
                        actual_public_at=None,available_at=stamp,historical_eligible=True,historical_version_verified=True,
                        availability_basis='Immutable SEC filing acquired by this system now; public SEC acceptance retained separately',
                        version_group=ticker+'_'+row['form']+'_'+row['reportDate'],revision_of=None,evidence_class='original_disclosure'),get(url))
                updates[ticker]=stamp
            except Exception as exc:errors.append(dict(source=ticker,stage='SEC',error_type=type(exc).__name__))
        # Registered mutable issuer pages: refetch, retain each content version,
        # and discover releases only from those official landing pages.
        for source in sources:
            if source.get('ticker') not in c['tickers'] or urlsplit(source.get('url','')).hostname in ('www.sec.gov','data.sec.gov') or source.get('historical_version_verified'):
                continue
            if source.get('url') in seen_urls:continue
            seen_urls.add(source.get('url'))
            try:
                content=get(source['url']);fingerprint=hashlib.sha256(content).hexdigest()
                if fingerprint==source.get('sha256'):continue
                sid=source['source_id']+'_'+fingerprint[:12]
                if any(s['source_id']==sid for s in sources):continue
                revision={**source,'source_id':sid,'revision_of':source['source_id'], 'available_at':stamp,
                    'historical_eligible':False,'historical_version_verified':False,'actual_public_at':None,
                    'availability_basis':'Mutable official page version first acquired now; not backdated'}
                archive(revision,content)
            except Exception as exc:errors.append(dict(source=source['source_id'],stage='issuer',error_type=type(exc).__name__))
        for ticker in c['tickers']:
            try:
                from bs4 import BeautifulSoup
                listing=IR_LISTINGS[ticker]
                allowed.add(urlsplit(listing).hostname)
                soup=BeautifulSoup(get(listing),'html.parser')
                urls=list(dict.fromkeys(urljoin(listing,a['href']).split('#')[0] for a in soup.select('a[href]')
                    if urlsplit(urljoin(listing,a['href'])).hostname==urlsplit(listing).hostname and
                    re.search(r'press-release-details|/press-release/|/news/\d|/news-releases/',a['href'])))
                for url in urls[:5]:
                    if any(s.get('url')==url for s in sources):continue
                    content=get(url);fingerprint=hashlib.sha256(content).hexdigest()
                    sid=ticker+'_IR_'+fingerprint[:20]
                    if any(s['source_id']==sid for s in sources):continue
                    archive(dict(source_id=sid,ticker=ticker,kind='issuer_release',title=ticker+' official release',
                        url=url,publisher='Official issuer',published_at=None,actual_public_at=None,available_at=stamp,
                        historical_eligible=False,historical_version_verified=False,report_period=None,revision_of=None,
                        version_group=ticker+'_IR_'+hashlib.sha256(url.encode()).hexdigest()[:16],
                        availability_basis='Official page acquired now; first public time remains unverified',
                        evidence_class='original_disclosure'),content)
            except Exception as exc:errors.append(dict(source=ticker,stage='issuer_discovery',error_type=type(exc).__name__))
        return dict(documents=documents,errors=errors,checkpoints=updates,requests=total,collected_at=stamp)

class KnowledgeMaintenance:
    def __init__(self,root=ROOT,collector=None):
        self.root=Path(root);self.state=self.root/'state/kb';self.state.mkdir(parents=True,exist_ok=True)
        self.path=self.state/'maintenance.sqlite3';self.collector=collector or OfficialCollector(root)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,status TEXT,config TEXT,next_times TEXT);
            CREATE TABLE IF NOT EXISTS drafts(hash TEXT PRIMARY KEY,config TEXT,preview TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,started TEXT,lease_until TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY,requests INTEGER NOT NULL);''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def status(self):
        with self.db() as db:
            row=db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            runs=[{**dict(r),'result':json.loads(r['result']) if r['result'] else None} for r in db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 20')]
        snapshot=active_snapshot(self.root)
        return dict(status=row['status'] if row else 'not_enabled',config=json.loads(row['config']) if row else dict(DEFAULTS),
                    next_times=json.loads(row['next_times']) if row else None,generation=snapshot.generation,runs=runs)

    def preview(self,config):
        c=validate(config);preview=dict(config=c,future_runs=next_times(c),timezone=c['display_timezone'],
            lifecycle='until paused',sources='registered SEC/issuer originals',llm_calls=0,
            note='Knowledge maintenance does not activate stock monitoring or paid research')
        from monitor_store import _changes
        with self.db() as db:old=db.execute('SELECT config FROM settings WHERE id=1').fetchone()
        previous=json.loads(old['config']) if old else None
        preview.update(base_config_sha256=sha(previous),changes=_changes(previous,c))
        token=sha(preview)
        with self.db() as db:db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?)',(token,encoded(c),encoded(preview)))
        return dict(status='draft',confirmation_hash=token,**preview)

    def confirm(self,token):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM drafts WHERE hash=?',(token,)).fetchone()
            if not row:raise ValueError('Preview and confirmation required')
            config=json.loads(row['config']);schedule=next_times(config)
            old=db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            if old and old['status']=='active' and old['config']==row['config']:return self.status()
            expected=json.loads(row['preview'])['base_config_sha256']
            if sha(json.loads(old['config']) if old else None)!=expected:raise ValueError('Maintenance configuration changed; preview again')
            # Fence a previously claimed job before replacing its confirmed settings.
            db.execute("UPDATE runs SET status='configuration_superseded' WHERE status='running'")
            db.execute('INSERT OR REPLACE INTO settings VALUES (1,?,?,?)',('active',row['config'],encoded(schedule)))
        return self.status()

    def action(self,action):
        if action not in ('pause','resume','end'):raise ValueError('Invalid maintenance action')
        with self.db() as db:
            row=db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            if not row:raise ValueError('Confirm a configuration first')
            if row['status']=='ended':raise ValueError('Ended maintenance needs a new confirmed configuration')
            schedule=next_times(json.loads(row['config'])) if action=='resume' else json.loads(row['next_times'])
            db.execute('UPDATE settings SET status=?,next_times=? WHERE id=1',({'pause':'paused','resume':'active','end':'ended'}[action],encoded(schedule)))
        return self.status()

    def tick(self,at=None):
        at=utc_datetime(at);owner=uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE runs SET status='interrupted_requires_review' WHERE status='running' AND lease_until<=?",(at.isoformat(),))
            if db.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():return None
            row=db.execute('SELECT * FROM settings WHERE id=1').fetchone()
            if not row or row['status']!='active':return None
            c=json.loads(row['config']);due=json.loads(row['next_times'])
            kind=next((k for k in ('audit','ingest') if utc_datetime(due[k][0])<=at),None)
            if not kind:return None
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,NULL)',(owner,kind,'running',at.isoformat(),(at+timedelta(seconds=120)).isoformat()))
        stop=threading.Event()
        def guard():
            with self.db() as db:
                active=db.execute("SELECT 1 FROM runs r,settings s WHERE r.id=? AND r.status='running' AND r.lease_until>? AND s.id=1 AND s.status='active'",(owner,now())).fetchone()
                if not active:raise RuntimeError('Maintenance lease or plan no longer active')
        def heartbeat():
            while not stop.wait(20):
                try:
                    with self.db() as db:db.execute("UPDATE runs SET lease_until=? WHERE id=? AND status='running' AND lease_until>?",((utc_datetime()+timedelta(seconds=120)).isoformat(),owner,now()))
                except Exception:return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            guard()
            if kind=='audit':result=KnowledgeVersions(self.root).audit()
            else:
                def reserve():
                    guard()
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE');day=utc_datetime().date().isoformat()
                        old=db.execute('SELECT requests FROM usage WHERE day=?',(day,)).fetchone();amount=old[0] if old else 0
                        if amount>=c['requests_per_day']:raise MaintenanceBudgetStop('Daily maintenance budget reached')
                        db.execute('INSERT OR REPLACE INTO usage VALUES (?,?)',(day,amount+1))
                collected=self.collector.collect(c,before_request=reserve,check_owner=guard)
                if collected['documents']:
                    result=KnowledgeVersions(self.root).publish(collected['documents'],check_owner=guard)
                else:result=dict(status='no_new' if not collected['errors'] else 'partial_source_failure')
                result.update(errors=collected['errors'],requests=collected['requests'])
                # Only successful source scans advance their own checkpoints, after publication.
                if collected['checkpoints']:
                    path=self.state/'checkpoints.json';old=load(path) if path.exists() else {};old.update(collected['checkpoints'])
                    temp=path.with_suffix('.tmp');temp.write_text(encoded(old),encoding='utf-8');guard();os.replace(temp,path)
            guard();status='completed' if result.get('status') not in ('partial_source_failure',) and not result.get('errors') else 'partial'
        except Exception as exc:
            result=dict(status='blocked_budget' if isinstance(exc,MaintenanceBudgetStop) else 'failed',error_type=type(exc).__name__);status='failed'
        finally:stop.set();thread.join(timeout=2)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            active=db.execute("SELECT 1 FROM runs r JOIN settings s ON s.id=1 WHERE r.id=? AND r.status='running' AND r.lease_until>? AND s.status='active' AND s.config=?",(owner,now(),encoded(c))).fetchone()
            if not active:
                db.execute("UPDATE runs SET status='interrupted_requires_review',result=? WHERE id=? AND status='running'",(encoded(result),owner))
                return dict(status='interrupted_requires_review',result=result)
            db.execute("UPDATE runs SET status=?,result=? WHERE id=? AND status='running'",(status,encoded(result),owner))
            # Collapse missed executions into one scan; never replay every missed slot.
            if result.get('status')=='blocked_budget':
                retry=(utc_datetime()+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
                due[kind]=[retry.isoformat(),*next_times(c,retry)[kind][:2]]
            elif status in ('failed','partial'):
                failures=db.execute("SELECT COUNT(*) FROM runs WHERE kind=? AND status IN ('failed','partial') AND started>=?",(kind,(utc_datetime()-timedelta(hours=1)).isoformat())).fetchone()[0]
                if failures<3:
                    retry=utc_datetime()+timedelta(seconds=60*2**max(0,failures-1))
                    due[kind]=[retry.isoformat(),*next_times(c,retry)[kind][:2]]
                else:due[kind]=next_times(c,utc_datetime())[kind]
            else:due[kind]=next_times(c,utc_datetime())[kind]
            db.execute('UPDATE settings SET next_times=? WHERE id=1',(encoded(due),))
        return result
