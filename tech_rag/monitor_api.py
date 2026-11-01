"""Local HTTP application boundary for explicit monitor-plan confirmation."""
import importlib.util
from pathlib import Path
from datetime import datetime,timezone
from common import ROOT
from aliases import resolve_company
from monitor_schedule import default_config,calendar_status,validate_config
from monitor_store import MonitorStore,dependency_blockers
from monitor_planner import MonitorPlanner
from monitor_runtime import MonitorRuntime
from monitor_agent import MonitorNewsAgent

class MonitorAPI:
    def __init__(self,*,state_dir=None,planner=None,store=None,runtime=None):
        from integration.source_config import configure_sources
        configure_sources()
        self.state=Path(state_dir or ROOT/'state/monitor');self.state.mkdir(parents=True,exist_ok=True)
        self.planner=planner or MonitorPlanner(state_dir=self.state/'planner')
        self.store=store or MonitorStore(self.state/'plans.sqlite3')
        self.runtime=runtime or MonitorRuntime(self.planner,state_dir=self.state,store=self.store,agent=MonitorNewsAgent(self.state))
        from event_analysis import DailyEventMarket,EventEvidence
        from knowledge_maintenance import KnowledgeMaintenance
        self.evidence=EventEvidence(self.state/'event_evidence.sqlite3')
        self.runtime.evidence=self.evidence
        if runtime is None:self.runtime.market=DailyEventMarket()
        self.maintenance=KnowledgeMaintenance()
        self.runtime.maintenance=self.maintenance

    def dependencies(self):
        from integration.source_config import configure_sources
        news_ready=all(importlib.util.find_spec(m) is not None for m in ('yfinance','curl_cffi','bs4','requests'))
        return dict(data_sources=configure_sources().status(),calendar=calendar_status(),news=dict(ready=news_ready,status='configured_unverified' if news_ready else 'pending_integration',
            provider='Yahoo via existing industry_news',reason='已安装适配器，需运行时验证网络；相关性抽样，非完整历史档案。'),
            agent=MonitorNewsAgent.readiness(),market=dict(ready=news_ready,status='configured_unverified' if news_ready else 'pending_integration',reason='日线适配器已接入；需核实首发时间，来源失败和未完成窗口保持未知，盘中响应未知。'),
            knowledge=dict(ready=(ROOT/'output/chunks.jsonl').is_file(),status=self.maintenance.status()['status'],reason='全局知识库维护独立确认；新关系待人工审核。'),
            prior_guidance=dict(ready=True,status='configured',reason='支持有原文、时间和相同口径的人工核实基线；没有基线时未知。'),
            historical_expectations=dict(ready=False,status='pending_integration',reason='可得一致预期和完整历史新闻档案待接入，禁止使用事后预期。'),
            native_search=dict(ready=False,status='hard_stopped',reason='旧原生搜索用量未知；保留原硬停止，周期监控不使用。'),
            notifications=dict(ready=True,status='in_app',reason='结果保存到本机，可在工作区查看；未接入站外推送。'))

    def _guide(self,gid):
        out=self.planner.get_guide(gid)
        guide=out['guide'] if 'guide' in out else out
        if not guide.get('rag'):raise ValueError('旧指南未冻结，请重新生成并确认。')
        return guide

    def get(self,path,query):
        def q(key,default=None):
            value=query.get(key,default)
            return value[0] if isinstance(value,list) and value else value
        if path=='/api/monitor/status':
            return dict(dependencies=self.dependencies(),worker=self.runtime.worker_status(),defaults=default_config('NVDA'),
                        supported_tickers=['NVDA','MU','TSM'],version='monitor-v1')
        if path=='/api/monitor/plans':return dict(plans=self.store.list_plans())
        if path=='/api/monitor/runs':return dict(runs=self.store.list_runs(q('plan_id')))
        if path=='/api/monitor/events':return dict(events=self.runtime.events(q('plan_id'),q('as_of')))
        if path=='/api/monitor/guide':return self.planner.get_guide(q('id'))
        if path=='/api/monitor/knowledge/status':return self.maintenance.status()
        if path=='/api/monitor/event-evidence':return self.evidence.latest(q('event_id'),q('as_of'))
        if path=='/api/monitor/event-evidence/candidates':
            event=next((e for e in self.runtime.events() if e['event_id']==q('event_id')),None)
            if not event:raise ValueError('Unknown saved event')
            from research import build_research_context
            packet=build_research_context(event['ticker'],event['title']+' 此前指引 guidance forecast outlook','current',{'use_dense':False,'max_results':12})
            return dict(status='unreviewed_candidates',event_id=event['event_id'],evidence=packet['citations'],
                note='Retrieved passages are not automatically forecasts or proof of prior expectations; review scope and source clocks.')
        if path=='/api/monitor/memory':
            from integration.research_memory import ResearchMemory,now
            return dict(lessons=ResearchMemory().context(q('ticker','NVDA'),q('as_of') or now(),
                namespace=q('namespace','production'),arm=q('arm','B')),fact_promotion_allowed=False)
        raise ValueError('未知监控接口')

    def post(self,path,body):
        if not isinstance(body,dict):raise ValueError('请求必须为JSON对象')
        if path=='/api/monitor/knowledge/preview':return self.maintenance.preview(body.get('config',{}))
        if path=='/api/monitor/knowledge/confirm':return self.maintenance.confirm(body.get('confirmation_hash'))
        if path=='/api/monitor/knowledge/action':return self.maintenance.action(body.get('action'))
        if path=='/api/monitor/knowledge/audit':
            from knowledge_versions import KnowledgeVersions
            return KnowledgeVersions().audit()
        if path=='/api/monitor/event-evidence':
            event=next((e for e in self.runtime.events() if e['event_id']==body.get('event_id')),None)
            if event is None:raise ValueError('Unknown saved event')
            if body['evidence'].get('ticker',event['ticker'])!=event['ticker']:raise ValueError('Event evidence ticker mismatch')
            result=self.evidence.append(event['event_id'],body['evidence'],source_resolver=self._resolve_original)
            if self.store.get_plan(event['plan_id'])['status']=='active':
                from datetime import timedelta
                self.store.enqueue_followup(event['plan_id'],event['event_id'],(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
            return result
        if path=='/api/monitor/guide':
            if type(body.get('refresh',False)) is not bool:raise ValueError('refresh必须为布尔值')
            return self.planner.guide(body.get('ticker','NVDA'),body.get('as_of') or 'current',refresh=body.get('refresh',False))
        if path=='/api/monitor/full-research':
            import uuid
            from integration.us_research import run_full_research
            from integration.effect_cost import BudgetStop
            try:
                return run_full_research(body.get('ticker','NVDA'),directory=self.state/'full_research'/('US_'+uuid.uuid4().hex),
                    paid_confirmed=body.get('paid_confirmed',False),arm=body.get('arm','B'),
                    memory_enabled=body.get('memory_enabled',True),event_analysis_versions=self._event_snapshots(body.get('ticker','NVDA')))
            except BudgetStop:
                return dict(status='blocked_budget',final=None,reason='原账本停止或不足以支持完整十二角色研究；未发起模型或工具请求。')
        if path=='/api/monitor/memory-review':
            from integration.research_memory import ResearchMemory
            return dict(review_id=ResearchMemory().review(body['memory_id'],body['checked_at'],body['observations'],body['lesson']),
                fact_promotion_allowed=False)
        if path=='/api/monitor/memory-settle':
            from integration.research_memory import ResearchMemory,settle_memory
            return settle_memory(ResearchMemory(),namespace=body.get('namespace','production'))
        if path=='/api/monitor/event-task':
            return self.planner.event_task(body.get('ticker','NVDA'),body.get('event',{}),as_of=body.get('as_of'))
        if path=='/api/monitor/prompt-research':
            from integration.prompt_web_research import run_prompt_research
            from integration.effect_cost import BudgetStop
            guide=self._guide(body.get('guide_id'))
            try:return run_prompt_research(body.get('ticker','NVDA'),guide=guide,directory=self.state/'prompt_research',
                paid_confirmed=body.get('paid_confirmed',False),event_analysis_versions=self._event_snapshots(body.get('ticker','NVDA')))
            except BudgetStop:
                return dict(status='blocked_budget',final=None,reason='原生联网原账本用量未知或预算停止；本次有限A/B授权不扩展到交互入口，没有发出新请求。')
        if path=='/api/monitor/draft':
            config=validate_config(body.get('config'))
            config['ticker']=resolve_company(config['ticker'])['research_symbol']
            guide=self._guide(body.get('guide_id'))
            if guide['ticker']!=config['ticker']:raise ValueError('监控指南和目标股票不一致')
            if not guide.get('topics'):raise ValueError('当前知识库没有可执行监控主题，暂不能启用。')
            if guide.get('status')!='ready':raise ValueError('当前时点产业链证据不足，不能把待查指南启用为已知监控范围。')
            ids={t['id'] for t in guide['topics']}
            if not set(config.get('topic_ids',ids))<=ids or not set(config.get('topic_priorities',{}))<=ids:
                raise ValueError('所选主题或优先级不属于本版监控指南。')
            return self.store.save_draft(config,guide['guide_id'],body.get('plan_id'))
        if path=='/api/monitor/preview':
            result=self.store.preview(body.get('draft_id'),self.dependencies())
            result['topics']=[{**t,'priority':result['config'].get('topic_priorities',{}).get(t['id'],t['priority'])}
                for t in self._guide(result['guide_id'])['topics'] if t['id'] in result['config'].get('topic_ids',[t['id']])]
            result['budget_note']='请求在每次实际传输之前计数；余额不足时延期至次日，频率不代表预算能覆盖所有轮次。模型另受既有全局账本门禁。'
            result['holiday_policy']='使用真实市场日历区分交易/休市；休市继续按已确认的非交易频率搜索，收盘汇总仅交易日运行。'
            return result
        if path=='/api/monitor/confirm':
            draft=self.store.get_draft(body.get('draft_id'))
            guide=self._guide(draft['guide_id'])
            if guide['ticker']!=draft['config']['ticker'] or not guide.get('topics'):raise ValueError('指南依赖失效')
            return self.store.confirm(body.get('draft_id'),body.get('confirmation_hash'),self.dependencies())
        if path=='/api/monitor/action':
            action=body.get('action');plan=self.store.get_plan(body.get('plan_id'))
            if action=='resume':
                blockers=dependency_blockers(self.dependencies(),plan['config'])
                if blockers:raise ValueError('无法恢复：'+';'.join(blockers))
            return self.store.action(plan['plan_id'],action)
        raise ValueError('未知监控接口')

    def _resolve_original(self,version_id):
        import json
        from knowledge_versions import active_snapshot,file_hash,safe_path
        with self.runtime.data.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM body_cache')]
        for row in rows:
            if row.get('version_id')==version_id and row.get('eligible'):
                raw=Path(row.get('raw_path',''))
                if not raw.is_file() or not raw.resolve().is_relative_to(self.state.resolve()) or file_hash(raw)!=row.get('raw_sha256'):return None
                return dict(text=row.get('body_text',''),url=row.get('url'),published_at=row.get('published_at'),acquired_at=row.get('retrieved_at'))
        snapshot=active_snapshot()
        for source in __import__('common').load(snapshot.file('source_catalog.json')):
            if version_id not in (source['source_id'],source.get('sha256')) or not source.get('local_path') or not source.get('text_path'):continue
            if file_hash(safe_path(snapshot.root,source['local_path']))!=source['sha256']:return None
            blocks=__import__('common').readl(safe_path(snapshot.root,source['text_path']))
            return dict(text='\n'.join(b['text'] for b in blocks),url=source['url'],published_at=source.get('published_at'),acquired_at=source.get('retrieved_at'))
        return None

    def _event_snapshots(self,ticker):
        # Only saved backend versions are injected, never caller-supplied metric prose.
        return [{k:e.get(k) for k in ('event_id','ticker','judged_at','versions','evidence_version','first_public_at',
                'first_public_status','assessment','classification')} for e in self.runtime.events() if e['ticker']==ticker.upper()][:10]
