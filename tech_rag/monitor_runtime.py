"""Confirmed-plan runner. News is evidence, never an order or a price forecast.

The HTTP browser is not the clock. A backend worker claims persistent jobs;
external errors never advance successful search positions. Yahoo is sampled
discovery, not an exhaustive point-in-time archive.
"""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,math,re,sqlite3,threading
from urllib.parse import urlsplit
from common import ROOT,parse_time

UTC=timezone.utc
DIMENSIONS=('information_novelty','expectation_gap','market_reaction','fundamental_impact','uncertainty')

def utc(value=None):
    return parse_time(value).astimezone(UTC) if isinstance(value,str) else (value or datetime.now(UTC)).astimezone(UTC)

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()

class DailyBudgetStop(RuntimeError):pass
class PlanInactive(RuntimeError):pass

def price_metrics(bars,benchmark):
    """Arithmetic only; caller supplies aligned, quality-controlled observations."""
    result=dict(status='unknown',price_return_pct=None,volume_ratio=None,relative_return_pct=None,
                causal_attribution='unknown',unknowns=[])
    def valid(rows):
        return len(rows)>=2 and all(type(r.get('close')) in (int,float) and math.isfinite(r['close']) and r['close']>0 for r in rows)
    if not valid(bars):result['unknowns'].append('价格样本不足或无效');return result
    change=(bars[-1]['close']/bars[0]['close']-1)*100
    result.update(status='observed',price_return_pct=round(change,6))
    vols=[r.get('volume') for r in bars]
    if all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in vols) and sum(vols[:-1])>0:
        result['volume_ratio']=round(vols[-1]/(sum(vols[:-1])/len(vols[:-1])),6)
    else:result['unknowns'].append('成交量基准不足')
    aligned=(len(benchmark)==len(bars) and all(a.get('time')==b.get('time') for a,b in zip(bars,benchmark)))
    if valid(benchmark) and aligned:
        result['relative_return_pct']=round(change-(benchmark[-1]['close']/benchmark[0]['close']-1)*100,6)
    else:result['unknowns'].append('缺少对齐的市场基准，相对表现未知')
    return result

def empty_assessment(reason='尚未核实'):
    return {k:{'status':'unknown','summary':reason,'evidence_ids':[]} for k in DIMENSIONS}

def classify_research(assessment):
    """Classification follows explicit research dimensions, never headline tone."""
    novelty=assessment.get('information_novelty',{}).get('status')
    gap=assessment.get('expectation_gap',{}).get('status')
    market=assessment.get('market_reaction',{}).get('status')
    if novelty=='no_new_fact':return 'archive_duplicate'
    if market in ('observed','daily_observation_available'):return 'market_reaction_observed'
    if novelty=='new_fact' and gap=='comparable_difference':
        comparison=assessment['expectation_gap'].get('comparison',{})
        differences=comparison.get('difference_vs_issuer_guidance',[])+comparison.get('difference_vs_named_forecast',[])
        if differences and any(d['position']!='within_range' for d in differences):return 'priority_verification'
        if differences:return 'routine_tracking'
    if novelty=='new_fact' and gap=='credible_difference':return 'priority_verification'
    if novelty=='new_fact' and gap=='consistent':return 'routine_tracking'
    return 'insufficient_information'

class ResearchData:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS usage (plan_id TEXT, day TEXT, kind TEXT, amount INTEGER NOT NULL, PRIMARY KEY(plan_id,day,kind));
            CREATE TABLE IF NOT EXISTS shared_news (news_id TEXT PRIMARY KEY,payload TEXT NOT NULL,first_seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS body_cache (news_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS discovery_cache (cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,news_id TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(plan_id,news_id));
            CREATE TABLE IF NOT EXISTS judgments (event_id TEXT,version INTEGER,payload TEXT NOT NULL,PRIMARY KEY(event_id,version));
            CREATE TABLE IF NOT EXISTS agent_calls (plan_id TEXT PRIMARY KEY,at_utc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS topic_positions (plan_id TEXT,topic_id TEXT,at_utc TEXT NOT NULL,PRIMARY KEY(plan_id,topic_id));
            ''')
    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=20)
        connection.row_factory=sqlite3.Row
        try:
            with connection:yield connection
        finally:connection.close()
    def reserve(self,plan_id,kind,amount,cap,at):
        day=utc(at).date().isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT amount FROM usage WHERE plan_id=? AND day=? AND kind=?',(plan_id,day,kind)).fetchone()
            total=old[0] if old else 0
            if total+amount>cap:raise DailyBudgetStop('每日'+kind+'预算不足')
            db.execute('INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(plan_id,day,kind) DO UPDATE SET amount=excluded.amount',(plan_id,day,kind,total+amount))
    def usage(self,plan_id,at=None):
        with self.db() as db:return {r['kind']:r['amount'] for r in db.execute('SELECT kind,amount FROM usage WHERE plan_id=? AND day=?',(plan_id,utc(at).date().isoformat()))}
    def append_judgment(self,event_id,payload):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM judgments WHERE event_id=?',(event_id,)).fetchone()[0]
            db.execute('INSERT INTO judgments VALUES (?,?,?)',(event_id,version,encoded(payload)))
        return version

class MonitorRuntime:
    def __init__(self,planner,*,state_dir=None,search=None,fetch=None,agent=None,market=None,store=None):
        self.state=Path(state_dir or ROOT/'state/monitor');self.state.mkdir(parents=True,exist_ok=True)
        self.data=ResearchData(self.state/'research.sqlite3');self.planner=planner;self.store=store
        self._search=search;self._fetch=fetch;self.agent=agent;self.market=market
        self._stop=threading.Event();self.thread=None;self.last_tick=None;self.last_error=None
        self._execution_lock=threading.Lock()
        self._request_context=threading.local()
        self.maintenance=None
        self.evidence=None

    def search(self,task,as_of,**kwargs):
        if self._search:return self._search(task,as_of,**kwargs)
        from integration.industry_news import search_yahoo
        return search_yahoo(task,as_of,max_results=3,cache_dir=self.state/'discovery',transport_limit=10,
                            before_request=getattr(self._request_context,'reserve',None))

    def fetch(self,item,as_of,**kwargs):
        if self._fetch:return self._fetch(item,as_of,**kwargs)
        from integration.news_body import fetch_article
        return fetch_article(item,as_of,archive_dir=self.state/'originals',max_chars=9000,
                             before_request=getattr(self._request_context,'reserve',None))

    def events(self,plan_id=None,as_of=None):
        cutoff=utc(as_of) if as_of else None;out=[]
        with self.data.db() as db:
            rows=db.execute('SELECT * FROM events'+(' WHERE plan_id=?' if plan_id else '')+' ORDER BY rowid DESC', (plan_id,) if plan_id else ()).fetchall()
            for row in rows[:300]:
                base=json.loads(row['payload'])
                judgments=[json.loads(r[0]) for r in db.execute('SELECT payload FROM judgments WHERE event_id=? ORDER BY version',(row['event_id'],))]
                if cutoff:judgments=[r for r in judgments if utc(r['judged_at'])<=cutoff]
                if not judgments:continue
                base.update(judgments[-1]);base['versions']=len(judgments);out.append(base)
                public=base.get('published_at') or base.get('provider_published_at')
                if cutoff and (utc(base['first_seen_at'])>cutoff or not public or utc(public)>cutoff):out.pop()
        return out

    def _queries(self,guide,run):
        selected=run['config'].get('topic_ids')
        topics=[{**t,'priority':run['config'].get('topic_priorities',{}).get(t['id'],t['priority'])}
                for t in guide.get('topics',[]) if not selected or t['id'] in selected]
        if not topics:return []
        # Rotate across all themes over successive 30-minute slots. Each run
        # transmits only the active theme, not a whole guide/knowledge base.
        slot=int(utc(run['scheduled_at']).timestamp()//1800)
        ordered=topics[slot%len(topics):]+topics[:slot%len(topics)]
        return ordered[:2]

    def _require_active(self,plan):
        if self.store and self.store.get_plan(plan)['status']!='active':
            raise PlanInactive('计划已暂停或结束')
        run_id=getattr(self._request_context,'owner',None)
        if run_id and self.store:
            try:self.store.assert_owner(run_id)
            except ValueError:raise PlanInactive('运行租约或确认版本已失效') from None

    def execute(self,run,*,now=None):
        at=utc(now);stamp=at.isoformat();config=run['config'];plan=run['plan_id']
        def reserve_transport():
            self._require_active(plan)
            self.data.reserve(plan,'requests',1,config['budgets']['requests_per_day'],utc().isoformat())
        self._request_context.reserve=reserve_transport
        self._request_context.fixed_clock=now is not None
        base=dict(status='failed',kind=run['kind'],new_items=0,llm_calls=0,watermark=None,
                  judged_at=stamp,errors=[],coverage='Yahoo相关性抽样；不保证覆盖全部新闻；仅确认成功的查询推进检查点。')
        try:
            self._require_active(plan)
            if run['kind']=='guide_maintenance':
                guide=self.planner.guide(config['ticker'],refresh=True)
                base.update(status='success',guide_id=guide['guide_id'],note='已保存新指南版本；监控范围改变需要重新预览和确认，当前计划仍使用已确认指南。')
                return base
            if run['kind']=='fundamental':
                events=self.events(plan)
                from zoneinfo import ZoneInfo
                market_date=at.astimezone(ZoneInfo('America/New_York')).date()
                month_start=market_date.replace(day=1)
                quarter_start=market_date.replace(month=((market_date.month-1)//3)*3+1,day=1)
                windows={}
                for cadence in config['analysis']['fundamental'].get('cadences',[]):
                    start=month_start if cadence=='monthly' else quarter_start
                    selected=[e for e in events if utc(e.get('published_at') or e['first_seen_at']).astimezone(ZoneInfo('America/New_York')).date()>=start]
                    windows[cadence]=dict(start_date=start.isoformat(),end_date=market_date.isoformat(),
                        events=[dict(event_id=e['event_id'],assessment=e['assessment'],classification=e['classification']) for e in selected],
                        scope='saved event observations in this calendar period; not a full financial statement analysis')
                base.update(status='success',events=len(events),fundamental_windows=windows,fundamental_summary=[dict(event_id=e['event_id'],assessment=e['assessment'],classification=e['classification']) for e in events[:20]],note='按月／季度窗口整理已保存事件，不将其冒充完整财报分析；没有新增内容时零大模型调用。')
                return base
            if run['kind']=='event_followup':return self._followup(run,at,base)
            guide=self.planner.get_guide(run['guide_id'])
            if 'guide' in guide:guide=guide['guide']
            topics=self._queries(guide,run)
            if not topics:base['errors'].append('监控指南没有可执行主题');return base
            start=utc(run['watermark'])-timedelta(minutes=config['retrieval']['overlap_minutes']) if run.get('watermark') else at-timedelta(hours=config['retrieval']['lookback_hours'])
            # The checkpoint means successful ingestion-through-time, not that
            # this relevance-ranked provider delivered an exhaustive archive.
            outcomes=[];new=[];positions=[];agent_failed=False
            for topic in topics:
                self._require_active(plan)
                with self.data.db() as db:
                    position=db.execute('SELECT at_utc FROM topic_positions WHERE plan_id=? AND topic_id=?',(plan,topic['id'])).fetchone()
                # Each rotated query owns its checkpoint. A global checkpoint
                # cannot describe themes which were not searched last round.
                if position:start=utc(position[0])-timedelta(minutes=config['retrieval']['overlap_minutes'])
                elif len(guide.get('topics',[]))>1:start=at-timedelta(hours=config['retrieval']['lookback_hours'])
                query=topic.get('search_query') or ' '.join((topic.get('search_objects') or [])[:3]+(topic.get('keywords') or [])[:4])[:450]
                if not query:query=config['ticker']+' '+topic['title']
                task=dict(task_id=topic['id'],query_en=query,time_window={'start':start.isoformat(),'end':stamp})
                # Same exact query/window within a minute shares retrieval
                # across stocks; article acquisition is shared independently.
                key=digest([query,start.replace(second=0,microsecond=0).isoformat(),at.replace(second=0,microsecond=0).isoformat()])
                with self.data.db() as db:
                    old=db.execute('SELECT payload FROM discovery_cache WHERE cache_key=?',(key,)).fetchone()
                if old:
                    result=json.loads(old[0]);result['shared_cache_used']=True
                else:
                    # Yahoo SDK includes authentication HTTP calls. Reserve
                    # ten transport attempts conservatively per query; bodies
                    # reserve four including all allowed redirects. Unused
                    # reservations remain charged to stay below the cap.
                    self._require_active(plan)
                    if self._search:self.data.reserve(plan,'requests',10,config['budgets']['requests_per_day'],stamp)
                    result=self.search(task,stamp)
                    if any(e.get('type')=='DailyBudgetStop' for e in result.get('errors',[])):raise DailyBudgetStop('Transport budget reached')
                    if result.get('status') not in ('candidates','no_candidates','no_eligible_candidates'):
                        base['errors'].append('新闻源未成功返回：'+str(result.get('status','unknown')));return base
                    with self.data.db() as db:db.execute('INSERT OR REPLACE INTO discovery_cache VALUES (?,?)',(key,encoded(result)))
                outcomes.append({k:result.get(k) for k in ('status','network_calls','calls','real_network_executed','shared_cache_used')})
                pending=[]
                # Resume saved unfinished stages even if Yahoo no longer returns the item.
                with self.data.db() as db:
                    for saved in db.execute('SELECT * FROM events WHERE plan_id=?',(plan,)).fetchall():
                        saved_event=json.loads(saved['payload'])
                        if saved_event.get('topic_id')!=topic['id']:continue
                        latest=db.execute('SELECT payload FROM judgments WHERE event_id=? ORDER BY version DESC LIMIT 1',(saved['event_id'],)).fetchone()
                        state=json.loads(latest[0]).get('agent_status') if latest else 'pending_source_verification'
                        if state not in ('pending_source_verification','deferred_model_budget_or_cadence'):continue
                        article=db.execute('SELECT payload FROM shared_news WHERE news_id=?',(saved['news_id'],)).fetchone()
                        if article:pending.append(json.loads(article[0]))
                items=list({digest([i.get('url'),i.get('provider_published_at') or i.get('pubtime'),i.get('title')]):i for i in pending+result.get('items',[])}.values())
                for item in items[:6]:
                    public=item.get('provider_published_at') or item.get('pubtime')
                    if public:
                        try:
                            if utc(public)>at:continue
                            if utc(public)<start:
                                with self.data.db() as db:
                                    known=any(json.loads(r[0]).get('url')==item.get('url') for r in db.execute('SELECT payload FROM shared_news'))
                                if not known:continue
                        except ValueError:public=None
                    identity=digest([item.get('url'),public,item.get('title')])
                    with self.data.db() as db:
                        existing=db.execute('SELECT event_id FROM events WHERE plan_id=? AND news_id=?',(plan,identity)).fetchone()
                        if existing:
                            previous=db.execute('SELECT payload FROM judgments WHERE event_id=? ORDER BY version DESC LIMIT 1',(existing[0],)).fetchone()
                            if previous and json.loads(previous[0]).get('agent_status') not in (
                                'pending_source_verification','deferred_model_budget_or_cadence'):continue
                        old=db.execute('SELECT payload,first_seen_at FROM shared_news WHERE news_id=?',(identity,)).fetchone()
                        if old:item={**json.loads(old['payload']),**item};first_seen=old['first_seen_at']
                        else:
                            # Revisions of a known URL retain the original
                            # system discovery time; version time is separate.
                            family_rows=db.execute('SELECT payload,first_seen_at FROM shared_news ORDER BY first_seen_at').fetchall()
                            first_seen=next((r['first_seen_at'] for r in family_rows if json.loads(r['payload']).get('url')==item.get('url')),stamp)
                            db.execute('INSERT INTO shared_news VALUES (?,?,?)',(identity,encoded(item),first_seen))
                    event_id='EV_'+digest([plan,identity])[:24]
                    event=dict(event_id=event_id,plan_id=plan,news_id=identity,ticker=config['ticker'],title=item.get('title',''),url=item.get('url'),
                        provider_published_at=public,published_at=None,first_seen_at=first_seen,version_seen_at=stamp,family_id=digest(item.get('url')),
                        source_status='candidate_metadata',topic_id=topic['id'])
                    initial_task=self.planner.event_task(config['ticker'],event,as_of=stamp)
                    with self.data.db() as db:
                        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',(event_id,plan,identity,encoded(event)))
                    if not existing:
                        self.data.append_judgment(event_id,dict(judged_at=stamp,assessment=empty_assessment(),task=initial_task,
                            classification='insufficient_information',agent_status='pending_source_verification'))
                    with self.data.db() as db:
                        cached=db.execute('SELECT payload FROM body_cache WHERE news_id=?',(identity,)).fetchone()
                    if cached:body=json.loads(cached[0])
                    else:
                        self._require_active(plan)
                        if self._fetch:self.data.reserve(plan,'requests',4,config['budgets']['requests_per_day'],stamp)
                        body=self.fetch(item,stamp)
                        # Failed acquisition is retried on a scheduled review;
                        # it is not promoted to an empty verified article.
                        if body.get('eligible'):
                            with self.data.db() as db:db.execute('INSERT OR REPLACE INTO body_cache VALUES (?,?)',(identity,encoded(body)))
                    event.update(published_at=body.get('published_at'),source_status=body.get('status','candidate_metadata'),original_version=body.get('version_id'))
                    task_out=self.planner.event_task(config['ticker'],event,as_of=stamp)
                    assessment=empty_assessment('仅发现候选；新增事实、事前预期和业务关联尚待核实')
                    agent_status='pending_source_verification'
                    if body.get('eligible') and body.get('body_acquired') and self.agent:
                        call_ok=self._can_call_agent(plan,config,at)
                        if call_ok:
                            self._require_active(plan)
                            self.data.reserve(plan,'llm',1,config['budgets']['llm_calls_per_day'],stamp)
                            # Record before request. Failure/unknown use cannot
                            # be hidden by a different retry/run identifier.
                            with self.data.db() as db:db.execute('INSERT OR REPLACE INTO agent_calls VALUES (?,?)',(plan,stamp))
                            base['llm_calls']+=1
                            try:
                                result_agent=self.agent(event=event,task=task_out,body=body,topic=topic,as_of=stamp)
                                assessment=self._validate_assessment(result_agent,body,assessment)
                                agent_status='research_pending_review'
                            except Exception as exc:
                                agent_failed=True;agent_status='agent_error';base['errors'].append('新闻研究调用失败：'+type(exc).__name__)
                        else:agent_status='deferred_model_budget_or_cadence'
                    self._analyse_event(run,event,assessment,stamp)
                    judgment=dict(judged_at=stamp if now is not None else utc().isoformat(),assessment=assessment,classification=classify_research(assessment),agent_status=agent_status,
                        task=task_out,remaining_actions=['核查首次公开时间及此前报道','补齐首次披露之前的可得预期','请求行情模块和排查同期混杂事件'],
                        market_request=self.market_request(config,event),fact_confidence='unknown',verification_priority='待核实',impact_path=topic.get('impact_path'))
                    judgment.update({k:event.get(k) for k in ('published_at','source_status','original_version','first_public_at','first_public_status','evidence_version')})
                    with self.data.db() as db:db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',(event_id,plan,identity,encoded(event)))
                    self._require_active(plan)
                    self.data.append_judgment(event_id,judgment)
                    if not existing:new.append(event_id)
                    self._schedule_reviews(run,event,at)
                    self._disclosure_reviews(run,event,body,at)
                positions.append((plan,topic['id'],stamp))
            if agent_failed:
                base.update(new_items=len(new),event_ids=new);return base
            if not self.store:self._commit_positions(positions)
            base.update(status='success' if new else 'no_new',new_items=len(new),event_ids=new,watermark=stamp,queries=outcomes,
                        topic_positions=positions,usage=self.data.usage(plan,stamp),
                        note='已保存待核实研究任务' if new else '本轮无新增（在已执行查询的抽样范围内）')
            return base
        except PlanInactive:
            base.update(status='blocked',errors=['计划已暂停或结束；不继续外部调用，成功处理位置未推进。']);return base
        except DailyBudgetStop:
            base.update(status='budget_exhausted',errors=['本日预算已达到，未继续外部调用；成功处理位置未推进。']);return base
        except Exception as exc:
            # Only error type reaches UI; provider/credential strings do not.
            base['errors'].append(type(exc).__name__);return base
        finally:
            self._request_context.reserve=None

    def _can_call_agent(self,plan,config,at):
        if config['budgets']['llm_calls_per_day']<=0:return False
        readiness=getattr(self.agent,'readiness',None)
        if readiness and readiness().get('ready') is not True:return False
        with self.data.db() as db:row=db.execute('SELECT at_utc FROM agent_calls WHERE plan_id=?',(plan,)).fetchone()
        return not row or at-utc(row[0])>=timedelta(minutes=config['cadence']['llm_min_minutes'])

    def _commit_positions(self,positions):
        with self.data.db() as db:
            db.executemany('INSERT INTO topic_positions VALUES (?,?,?) ON CONFLICT(plan_id,topic_id) DO UPDATE SET at_utc=excluded.at_utc',positions)

    @staticmethod
    def _validate_assessment(result,body,base):
        # A model supplies reviewable observations, not verification. Quotes
        # must occur in the actual article. Expectation gaps require separate
        # pre-disclosure baselines, so a single current article cannot fill it.
        text=body.get('body_text','')
        for row in (result or {}).get('findings',[])[:10]:
            if not isinstance(row,dict):continue
            dimension=row.get('dimension');quote=row.get('quote')
            if dimension not in ('information_novelty','fundamental_impact') or not isinstance(quote,str) or len(quote)<8 or quote not in text:continue
            base[dimension]={'status':'reported_unverified','summary':str(row.get('statement',''))[:350],
                             'evidence_ids':[body.get('version_id')],'quote':quote[:500]}
        base['uncertainty']={'status':'open','summary':'仍需历史预期、独立来源与行情核验；模型观察不等于核实结论。','evidence_ids':[]}
        return base

    @staticmethod
    def market_request(config,event):
        return dict(status='pending_verification',ticker=config['ticker'],market=config['market'],anchor=event.get('first_public_at'),
                    anchor_status=event.get('first_public_status','unknown'),
                    windows=config['analysis']['event'].get('trading_days',[]),metrics=['price_return','volume_ratio','relative_return'],
                    causal_attribution='unknown')

    def _analyse_event(self,run,event,assessment,stamp):
        from event_analysis import comparable_gap
        judged=stamp if getattr(self._request_context,'fixed_clock',False) else utc().isoformat()
        evidence=self.evidence.latest(event['event_id'],judged) if self.evidence else {}
        if evidence:
            event.update({k:v for k,v in evidence.items() if k not in ('baselines','event_facts')})
            event['evidence_version']=evidence['version']
        gap=comparable_gap(event,evidence.get('baselines',[]),judged)
        novelty=evidence.get('novelty_review')
        if novelty:
            assessment['information_novelty']=dict(status=novelty['status'],summary=novelty['reason'],
                evidence_ids=[v['version_id'] for v in evidence.get('event_facts',[])],reviewed_by=evidence['reviewed_by'])
        assessment['expectation_gap']={'status':gap['status'],'summary':'分开比较公司指引、具名预测与市场一致预期；缺项未知。',
                                       'comparison':gap,'evidence_ids':[b.get('version_id') for b in evidence.get('baselines',[])]}
        if self.market and run['config']['analysis']['event']['enabled']:
            self._require_active(run['plan_id'])
            try:
                if getattr(self.market,'accounted_transport',False):
                    observed=self.market(self.market_request(run['config'],event),
                        as_of=stamp if getattr(self._request_context,'fixed_clock',False) else 'current',
                        before_request=getattr(self._request_context,'reserve',None))
                else:
                    self.data.reserve(run['plan_id'],'requests',2,run['config']['budgets']['requests_per_day'],stamp)
                    observed=self.market(self.market_request(run['config'],event),as_of=stamp)
            except (DailyBudgetStop,PlanInactive):raise
            except Exception as exc:observed=dict(status='unknown',error_type=type(exc).__name__)
            assessment['market_reaction']={'status':observed.get('status','unknown'),
                'summary':'程序计算的日线观察，不证明事件因果或已定价程度。','metrics':observed,'evidence_ids':[]}
        return assessment

    def _schedule_reviews(self,run,event,at):
        if not self.store:return
        from monitor_schedule import followup_times
        for point in followup_times(run['config'],event.get('published_at') or at.isoformat()):
            if utc(point)>at:self.store.enqueue_followup(run['plan_id'],event['event_id'],point)

    def _disclosure_reviews(self,run,event,body,at):
        """An acquired issuer financial disclosure is an explicit trigger.

        Title matching alone is insufficient: require an eligible original on
        the issuer's known investor host. This triggers research, never affirms
        previous hypotheses or identifies anonymous customers.
        """
        if not self.store or not run['config']['followup']['after_disclosure'] or not body.get('eligible'):return
        issuer_hosts={'NVDA':{'investor.nvidia.com'},'MU':{'investors.micron.com'},'TSM':{'investor.tsmc.com'}}
        host=(urlsplit(event.get('url') or '').hostname or '').lower()
        if host not in issuer_hosts.get(run['config']['ticker'],set()):return
        if not re.search(r'earnings|financial results|annual report|10-[kq]|20-f|6-k|财务|财报|业绩',event['title'],re.I):return
        for previous in self.events(run['plan_id'])[:100]:
            if previous['event_id']==event['event_id']:continue
            self.store.enqueue_followup(run['plan_id'],previous['event_id'],(at+timedelta(seconds=1)).isoformat())

    def _followup(self,run,at,base):
        event_id=run.get('event_id') or (run.get('payload') or {}).get('event_id')
        event=next((e for e in self.events(run['plan_id']) if e['event_id']==event_id),None)
        if not event:base['errors'].append('待复核事件不存在');return base
        stamp=at.isoformat();config=run['config']
        self._require_active(run['plan_id'])
        if self._fetch:self.data.reserve(run['plan_id'],'requests',4,config['budgets']['requests_per_day'],stamp)
        previous_version=event.get('original_version')
        body=self.fetch(event,stamp)
        if body.get('eligible'):
            event.update(published_at=body.get('published_at'),source_status=body.get('status','body_verified'),original_version=body.get('version_id'))
            with self.data.db() as db:db.execute('INSERT OR REPLACE INTO body_cache VALUES (?,?)',(event['news_id'],encoded(body)))
        task=self.planner.event_task(config['ticker'],event,as_of=stamp)
        assessment=empty_assessment('复核时点仍需核实历史预期、影响路径与行情。')
        status='pending_source_verification'
        unchanged=body.get('version_id') and body.get('version_id')==previous_version
        if unchanged:
            status=event.get('agent_status','research_pending_review')
            for dim in ('information_novelty','fundamental_impact'):
                if event.get('assessment',{}).get(dim):assessment[dim]=event['assessment'][dim]
        if body.get('eligible') and not unchanged and self.agent and self._can_call_agent(run['plan_id'],config,at):
            self._require_active(run['plan_id'])
            self.data.reserve(run['plan_id'],'llm',1,config['budgets']['llm_calls_per_day'],stamp)
            with self.data.db() as db:db.execute('INSERT OR REPLACE INTO agent_calls VALUES (?,?)',(run['plan_id'],stamp))
            base['llm_calls']=1
            assessment=self._validate_assessment(self.agent(event=event,task=task,body=body,topic={},as_of=stamp),body,assessment)
            status='research_pending_review'
        self._analyse_event(run,event,assessment,stamp)
        self._require_active(run['plan_id'])
        version=self.data.append_judgment(event_id,dict(judged_at=stamp if getattr(self._request_context,'fixed_clock',False) else utc().isoformat(),assessment=assessment,classification=classify_research(assessment),task=task,
            agent_status=status,published_at=event.get('published_at'),source_status=event.get('source_status'),original_version=event.get('original_version'),
            first_public_at=event.get('first_public_at'),first_public_status=event.get('first_public_status'),evidence_version=event.get('evidence_version'),
            remaining_actions=['继续核实事前预期与经营影响；不得仅因价格变化结束基本面跟踪。']))
        base.update(status='success',research_status='pending_verification',event_id=event_id,judgment_version=version,
            note='本轮采集与任务保存完成；研究核实尚未完成，未核实字段仍为未知。');return base

    def start(self):
        if self.thread and self.thread.is_alive():return
        self._stop.clear()
        self.thread=threading.Thread(target=self._loop,name='news-monitor-worker',daemon=True);self.thread.start()
    def stop(self):self._stop.set()
    def worker_status(self):return dict(running=bool(self.thread and self.thread.is_alive()),last_tick=self.last_tick,last_error=self.last_error,concurrency=1)
    def _loop(self):
        while not self._stop.is_set():
            try:
                self.last_tick=datetime.now(UTC).isoformat()
                run=self.store.claim_due() if self.store else None
                if run:
                    self.run_claimed(run)
                elif self.maintenance:self.maintenance.tick()
                self.last_error=None
            except Exception as exc:self.last_error=type(exc).__name__
            self._stop.wait(2)

    def run_claimed(self,run):
        """Renew while working; a recovered stale owner cannot commit or fetch."""
        with self._execution_lock:
            heartbeat_stop=threading.Event()
            def heartbeat():
                while not heartbeat_stop.wait(20):
                    try:self.store.renew_lease(run['run_id'])
                    except Exception:return
            self._request_context.owner=run['run_id']
            thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
            try:
                result=self.execute(run)
                self.store.assert_owner(run['run_id'])
                status='success' if result['status'] in ('success','no_new') else ('deferred' if result['status']=='budget_exhausted' else 'failed')
                self.store.finish_run(run['run_id'],status,result,watermark=result.get('watermark'),
                    error=';'.join(result.get('errors',[])) or None)
                if status=='success':self._commit_positions(result.get('topic_positions',[]))
                return result
            finally:
                heartbeat_stop.set();thread.join(timeout=2);self._request_context.owner=None
