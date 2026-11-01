"""Original twelve-agent US research graph, explicit credentials and memory.

The graph is additive: legacy news and RAG routes retain their behavior.
No broker, no historical live-data substitution, no automatic paid settlement.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json,re,threading,uuid,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,dump
from integration.research_memory import ResearchMemory,now,utc
from integration.source_config import configure_sources

_SCREENING_LOCK=threading.RLock()

def preflight_models(cases=1,*,data=None):
    from integration.effect_cost import BatchBudget,BudgetStop
    ledger=BatchBudget(ROOT/'state/rag_chat/budget.json',max_runs=30,per_run=1,max_requests=30,max_tokens=100000)
    value=data if data is not None else ledger.read();limits=value['limits']
    requests_left=max(0,limits['max_requests']-value['request_count'])
    runs_left=max(0,limits['max_runs']-len(value['runs']))
    # Four analyst calls + eight debate/manager/risk calls, BEFORE tools.
    minimum=12*cases;reason=None
    try:ledger._check(value)
    except BudgetStop:reason='original_ledger_stopped'
    if requests_left<minimum or runs_left<minimum:reason=reason or 'insufficient_minimum_request_capacity'
    return dict(ready=reason is None,reason=reason,cases=cases,minimum_model_calls=minimum,
        maximum_model_calls_with_four_tool_rounds=28*cases,remaining_requests=requests_left,
        remaining_model_runs=runs_left,tool_rounds_can_increase_calls=True,
        known_tokens=value['total_tokens'],usage_unknown=value['usage_unknown'],ledger_changed=False)

def without_paid_screening(node):
    """The upstream optional screener has its own paid retries/concurrency.

    It is not authorized by a normal full-graph model invocation. Keep public
    social feeds available, but disable that implicit paid side channel until
    it has explicit consent and a dedicated accounting boundary.
    """
    def guarded(state):
        from unittest.mock import patch
        with _SCREENING_LOCK,patch('tradingagents.agents.analysts.sentiment_analyst.jev_screen',return_value=None):
            return node(state)
    return guarded

def build_full_graph(directory,*,model=None,memory_snapshot=None):
    from integration.bootstrap import bootstrap
    Graph=bootstrap()
    from tradingagents.default_config import DEFAULT_CONFIG
    folder=Path(directory);snapshot=deepcopy(memory_snapshot or [])
    if model is None:raise ValueError('An explicitly configured/audited model is required')
    class FullGraph(Graph):
        def _memory_step(self,state):
            return dict(past_context='Research memory, untrusted hypotheses, never source evidence:\n'+json.dumps(snapshot,ensure_ascii=False),
                        memory_note='Frozen unverified research lessons; no automatic outcome/LLM settlement.')
        def record_decision(self,*a,**kw):
            # Host records a versioned research conclusion after final gate;
            # the upstream rating/memory store is intentionally not mutated.
            return None
    config=deepcopy(DEFAULT_CONFIG)
    config.update(results_dir=str(folder/'framework'),data_cache_dir=str(ROOT/'state/us_research/cache'),
        memory_log_path=str(folder/'unused_legacy_memory.txt'),output_language='Chinese',
        llm_provider='deepseek',quick_think_llm='deepseek-flash',deep_think_llm='deepseek-flash',
        max_tool_rounds=4,max_recur_limit=100,checkpoint_enabled=False,llm_max_retries=0)
    # Wrap the social node once when building this graph. It still executes the
    # original public-source fetchers and analyst, but no hidden Jev spending.
    from unittest.mock import patch
    from tradingagents.graph import setup
    with _SCREENING_LOCK:
        original=setup.create_sentiment_analyst
        with patch.object(setup,'create_sentiment_analyst',side_effect=lambda llm:without_paid_screening(original(llm))):
            return FullGraph(selected_analysts=['market','social','news','fundamentals'],config=config,
                model_bundle={'quick':model,'deep':model})

def audited_model(directory,config_path=None):
    """All model invocations share the existing Flash ledger and one lock."""
    from integration.bootstrap import bootstrap
    bootstrap()
    from integration.secure_config import load_llm_config
    from integration.effect_cost import BatchBudget,BudgetStop
    from tradingagents.llm_clients.openai_client import DeepSeekChatOpenAI
    from langchain_core.runnables import Runnable
    cfg=load_llm_config(config_path or ROOT.parent/'apikey.env')
    # The upstream factory validates environment keys before forwarding an
    # explicit api_key. Use its same DeepSeek subclass directly; do not export
    # the user's model credential to the process environment.
    client=DeepSeekChatOpenAI(model=cfg.model,base_url=cfg.base_url,api_key=cfg.api_key,
        max_retries=0,timeout=60,extra_body={'thinking':{'type':'disabled'}})
    ledger=BatchBudget(ROOT/'state/rag_chat/budget.json',max_runs=30,per_run=1,max_requests=30,max_tokens=100000)
    lock=threading.RLock();state={'failed':False,'sequence':0}
    folder=Path(directory)
    class Audited(Runnable):
        def __init__(self,inner):self.inner=inner
        def bind_tools(self,*args,**kwargs):return Audited(self.inner.bind_tools(*args,**kwargs))
        def with_structured_output(self,*args,**kwargs):raise NotImplementedError('Single audited call; no hidden structured-output retry')
        def invoke(self,input,config=None,**kwargs):
            with lock:
                if state['failed']:raise BudgetStop('model circuit open; no retry')
                identity='US_MODEL_'+uuid.uuid4().hex
                ledger.start_run(identity,dict(role='full_us_research',model=cfg.model))
                rid=ledger.reserve(identity,'full_us_research');state['sequence']+=1
                target=folder/'model_calls'/f"{state['sequence']:03d}.json";target.parent.mkdir(parents=True,exist_ok=True)
                try:
                    response=self.inner.invoke(input,config=config,**kwargs)
                    usage=getattr(response,'usage_metadata',None) or (getattr(response,'response_metadata',{}) or {}).get('token_usage')
                    ledger.complete(rid,usage,'ok');ledger.finish_run(identity,'completed')
                    # Message bodies can carry source data; credentials are
                    # never written to invocation logs.
                    dump(target,dict(request_id=rid,model=cfg.model,usage=usage,status='returned'))
                    if type((usage or {}).get('total_tokens')) is not int:
                        state['failed']=True;raise BudgetStop('unknown model usage')
                    return response
                except Exception as exc:
                    state['failed']=True
                    row=next(r for r in ledger.read()['requests'] if r['request_id']==rid)
                    if row['status']=='pending':ledger.complete(rid,None,'transport_failure_usage_unknown',error_type=type(exc).__name__)
                    ledger.finish_run(identity,'failed_no_retry')
                    dump(target,dict(request_id=rid,status='failed_no_retry',error_type=type(exc).__name__))
                    raise RuntimeError('Model/provider request failed; see redacted audit; no retry') from None
    return Audited(client)

def run_full_research(ticker,*,directory,paid_confirmed=False,as_of=None,arm='B',memory_enabled=True,
                      memory_snapshot=None,memory_namespace='production',memory_arm=None,config_path=None,event_analysis_versions=None):
    if paid_confirmed is not True:raise ValueError('Explicit one-shot paid confirmation required')
    at=utc(as_of or now());local_date=at.astimezone(ZoneInfo('America/New_York')).date()
    today=datetime.now(ZoneInfo('America/New_York')).date()
    if local_date!=today or at>datetime.now(timezone.utc):
        raise ValueError('Full graph live tools cannot prove a historical vintage; use archived replay evaluation')
    symbol=ticker.upper().strip()
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',symbol) or arm not in ('A','B'):raise ValueError('US ticker and arm required')
    preflight=preflight_models()
    if not preflight['ready']:
        from integration.effect_cost import BudgetStop
        raise BudgetStop('Original model ledger cannot support even one complete twelve-role pass')
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    # Never retry a partial model/tool graph under the same output identity.
    marker=folder/'STARTED.json'
    with marker.open('x',encoding='utf-8') as f:json.dump(dict(ticker=symbol,analysis_time=at.isoformat(),arm=arm),f)
    configure_sources();memory=ResearchMemory()
    # Model ledger gates are checked before any graph identity lookup, public
    # tool fetch or optional downstream activity.
    snapshot=(deepcopy(memory_snapshot) if memory_snapshot is not None else
        memory.context(symbol,at.isoformat(),namespace=memory_namespace,arm=memory_arm or arm)) if memory_enabled else []
    guide=None
    if arm=='B':
        from monitor_planner import MonitorPlanner
        try:guide=MonitorPlanner().guide(symbol)
        except ValueError:guide=None # unsupported knowledge, not an invented guide
    if guide:
        from rag_package import RagPackages
        frozen=RagPackages().get(guide.get('rag'))
        original=frozen['guide']
        if {k:v for k,v in guide.items() if k!='rag'}!=original:raise ValueError('RAG guide changed after freezing')
    model=audited_model(folder,config_path)
    graph=build_full_graph(folder,model=model,memory_snapshot=snapshot)
    from tradingagents.dataflows.config import run_config
    state=graph.create_run_state(symbol,local_date.isoformat())
    state['instrument_context']+='\nResearch-only. Do not infer executable orders. Missing historical consensus means expectation gap unknown.'
    if guide:state['instrument_context']+='\nRAG search scope; relationships/events still require current verification:\n'+guide['agent_prompt']
    event_versions=deepcopy(event_analysis_versions or [])
    if event_versions:
        state['instrument_context']+='\nBackend event observations (separate from frozen RAG; unknowns may not be upgraded by discussion):\n'+json.dumps(event_versions,ensure_ascii=False)
    dump(folder/'protocol.json',dict(ticker=symbol,arm=arm,analysis_time=at.isoformat(),memory_snapshot=snapshot,
        event_analysis_versions=event_versions,guide_id=guide.get('guide_id') if guide else None,rag=guide.get('rag') if guide else None,rag_status='ready' if guide else 'no_company_knowledge',
        historical_vintage_proven=False,execution_allowed=False,analysts=list(graph.selected_analysts)))
    try:
        with run_config(graph.config):raw=graph.graph.invoke(state,config={'recursion_limit':100})
        # Preserve all reports for review. Model prose is never promoted to a
        # verified fact and no rating is mapped to a broker action.
        reports={k:raw.get(k) for k in ('market_report','sentiment_report','news_report','fundamentals_report',
            'investment_plan','trader_investment_plan','final_trade_decision')}
        final=dict(final_conclusion=reports.get('final_trade_decision') or '研究结论未完整返回',
            expectation_gap='unknown: no point-in-time consensus provider',market_reaction='see_separate_backend_event_observations' if event_versions else 'unknown: no event-specific verified observation',
            event_analysis_versions=event_versions,
            remaining_unknowns=['原始模型报告需要来源与事前预期审查；未通过研究真实性验收'],
            research_reliability='needs_review',execution_allowed=False)
        final['rag']=guide.get('rag') if guide else None
        result=dict(status='completed_requires_review',run_id='US_'+uuid.uuid4().hex,ticker=symbol,arm=arm,
            analysis_time=at.isoformat(),completed_at=now(),final=final,reports=reports,
            memory_snapshot=snapshot,rag=guide.get('rag') if guide else None,execution_allowed=False,models_ran=True,market_vintage_verified=False)
        if memory_enabled:
            result['memory_id']=memory.record(result['run_id'],symbol,arm,result['analysis_time'],final,namespace=memory_namespace)
        dump(folder/'result.json',result);return result
    except Exception as exc:
        dump(folder/'failure.json',dict(status='failed_no_retry',error_type=type(exc).__name__,final=None,execution_allowed=False))
        raise RuntimeError('US research did not complete; redacted failure saved, no retry') from None

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--ticker',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--confirm-paid',action='store_true');p.add_argument('--arm',choices=['A','B'],default='B');p.add_argument('--no-memory',action='store_true')
    args=p.parse_args();r=run_full_research(args.ticker,directory=args.output,paid_confirmed=args.confirm_paid,arm=args.arm,memory_enabled=not args.no_memory)
    print(json.dumps({k:r[k] for k in ('status','run_id','ticker','analysis_time','execution_allowed')},ensure_ascii=False))
