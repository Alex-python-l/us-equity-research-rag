"""Evidence extension of the pinned *official* TradingAgents execution graph.

RAG is called only by its registered ToolNode inside the news subgraph. Other
roles are the official factory nodes, using new prompts and independent gates.
The controlled mode substitutes the model, not the graph or retrieval tools.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import time
import uuid

from integration.bootstrap import bootstrap, UPSTREAM_COMMIT, VENDOR
TradingAgentsGraph=bootstrap()
from tradingagents.agents.state import AgentState
from tradingagents.default_config import DEFAULT_CONFIG
from langchain_core.messages import AIMessage

from common import ROOT, sha
from lexical import freeze
from integration.governance import new_shared_state, append_claim, review_ledger, eligible_claims
from integration.runtime import EvidenceRuntime, GOVERNANCE_FIELDS, governance_copy
from integration.models import RoleModel
from integration.prompts import ROLES,load_prompt


class EvidenceState(AgentState):
    schema_version:str
    analysis_time:str
    historical:bool
    permissions:list
    evidence_registry:dict
    evidence_observations:dict
    assertion_registry:dict
    event_registry:dict
    claim_ledger:dict
    unresolved_gaps:list
    review_issues:list
    strategy_hypotheses:list
    research_gate:dict
    semantic_validation_limits:list
    received_event:dict
    news_candidates:list
    tool_trace:list
    evidence_packets:list
    tool_permissions:dict
    role_trace:list
    raw_role_outputs:list
    framework_metadata:dict
    run_id:str
    run_usage:dict


class EvidenceExtension:
    state_schema=EvidenceState
    output_fields={k:v for k,v in EvidenceState.__annotations__.items()}

    def __init__(self,runtime):
        self.runtime=runtime
        runtime.build_tools()
        runtime.models={role:RoleModel(runtime,role) for role in ROLES}

    def model_for(self,role,default):
        return self.runtime.models[role]

    def tools_for(self,key):
        if key!='news':raise ValueError('Only the time-safe research news analyst is enabled')
        return self.runtime.tools

    def intake(self,state):
        return self.runtime.intake(state)

    def wrap_node(self,role,official_node):
        def node(state):
            rt=self.runtime;rt.state=state
            model=rt.models[role];model.last_payload=None;model.last_raw=''
            before=time.perf_counter()
            update=official_node(state)
            shared=governance_copy(state)
            last=(update.get('messages') or [None])[-1]
            tool_turn=bool(getattr(last,'tool_calls',None))
            accepted=[];issues=[]
            if not tool_turn:
                payload=model.last_payload
                if not isinstance(payload,dict) or not isinstance(payload.get('claims'),list):
                    issues=[{'code':'invalid_role_json','role':role,'severity':'error'}]
                    shared['review_issues'].extend(issues)
                else:
                    for raw in payload['claims'][:4]:
                        result=append_claim(shared,raw,role)
                        issues.extend(result['issues'])
                        if result['claim'] and result['claim']['status']=='accepted':
                            accepted.append(result['claim'])
                # Free-form prose and rejected judgments never become the next
                # role's report. Preserve them only in the audit output.
                safe='\n'.join('[%s v%s] %s | evidence=%s | conditions=%s | unknowns=%s' %
                    (c['claim_id'],c['version'],c['text'],','.join(c['evidence_ids']),
                     json.dumps(c['conditions'],ensure_ascii=False),json.dumps(c['unknowns'],ensure_ascii=False))
                    for c in accepted) or 'needs_more_evidence: no new validated claim; see shared gaps and review issues.'
                self._sanitize(role,state,update,safe)
                review_ledger(shared)
            raw_outputs=[*state.get('raw_role_outputs',[]),
                {'role':role,'tool_turn':tool_turn,'raw_output':model.last_raw,
                 'parsed_payload':model.last_payload,'accepted_claim_ids':[c['claim_id'] for c in accepted],
                 'issue_codes':[i['code'] for i in issues]}]
            trace=[*state.get('role_trace',[]),
                {'role':role,'tool_turn':tool_turn,'factory':'official TradingAgents',
                 'mode':rt.mode,'analysis_time':rt.analysis_time,
                 'seconds':round(time.perf_counter()-before,4),
                 'accepted':len(accepted),'issue_codes':[i['code'] for i in issues],
                 'prompt_sha256':load_prompt(role)['sha256']}]
            return {**update,**shared,'raw_role_outputs':raw_outputs,'role_trace':trace}
        return node

    @staticmethod
    def _sanitize(role,state,update,safe):
        if role=='news':
            update['news_report']=safe
            update['messages']=[AIMessage(content=safe)]
        elif role in ('bull','bear'):
            label=role.title()+' Analyst: '+safe
            old=state['investment_debate_state'];d=update['investment_debate_state']
            d.update(history=old['history']+'\n'+label,current_response=label)
            d[role+'_history']=old.get(role+'_history','')+'\n'+label
        elif role=='research_manager':
            update['investment_plan']=safe;update['investment_debate_state']['current_response']=safe
        elif role=='trader':
            update['trader_investment_plan']=safe;update['messages']=[AIMessage(content=safe)]
        elif role in ('aggressive','conservative','neutral'):
            old=state['risk_debate_state'];d=update['risk_debate_state']
            d['history']=old['history']+'\n'+role+': '+safe
            d[role+'_history']=old.get(role+'_history','')+'\n'+safe
            d['current_'+role+'_response']=safe
        elif role=='portfolio_manager':
            update['final_trade_decision']=safe
            update['final_rating']='REVIEW'

    def final_gate(self,state):
        shared=governance_copy(state);review_ledger(shared)
        usable=eligible_claims(shared)
        shared['research_gate'].update(signal='no_signal',execution_allowed=False,
             currently_usable_claim_count=len(usable),
             currently_usable_claim_versions=[{'claim_id':c['claim_id'],'version':c['version']} for c in usable],
             reason='Research-only integration; no independently validated valuation/return bridge or executable signal rules.')
        return shared


class EvidenceTradingAgentsGraph(TradingAgentsGraph):
    def __init__(self,runtime):
        self.evidence_runtime=runtime;extension=EvidenceExtension(runtime)
        cfg=deepcopy(DEFAULT_CONFIG)
        cfg.update(results_dir=str(ROOT/'output/framework_runtime'),
            data_cache_dir=str(ROOT/'state/framework_cache'),
            memory_log_path=str(ROOT/'state/framework_disabled_memory.txt'),
            max_debate_rounds=1,max_risk_discuss_rounds=1,max_tool_rounds=3 if runtime.fetch_news else 2,
            max_recur_limit=50,checkpoint_enabled=False,llm_max_retries=0)
        super().__init__(selected_analysts=['news'],config=cfg,
            model_bundle={'quick':runtime.models['news'],'deep':runtime.models['research_manager']},
            evidence_extension=extension)

    def propagate(self,*args,**kwargs):
        raise RuntimeError('Use run_evidence(); default day-only/live-memory propagate is disabled')

    def run_evidence(self,event=None):
        rt=self.evidence_runtime;rt.event_input=event
        state=self.propagator.create_initial_state(rt.ticker,rt.analysis_time[:10],
            instrument_context=json.dumps(rt.company,ensure_ascii=False),
            past_context='No outcome-reflection memory: unvintaged/post-outcome data disabled.')
        state.update(new_shared_state(rt.analysis_time,rt.historical))
        state.update(run_id='TR3_'+uuid.uuid4().hex,tool_trace=[],evidence_packets=[],
            role_trace=[],raw_role_outputs=[],tool_permissions={},received_event={},news_candidates=[],run_usage={})
        patch_hashes={str(p.relative_to(VENDOR)):sha(p.read_text(encoding='utf-8')) for p in
            [VENDOR/'tradingagents/graph/setup.py',VENDOR/'tradingagents/graph/trading_graph.py']}
        state['framework_metadata']={'repository':'https://github.com/TauricResearch/TradingAgents',
            'upstream_commit':UPSTREAM_COMMIT,'version':'0.7.0','vendor_patch_hashes':patch_hashes,
            'execution':'official factories and official StateGraph with evidence extension',
            'model_mode':rt.mode,'backend':rt.backend,'post_outcome_memory_enabled':False,
            'prompt_hashes':{r:load_prompt(r)['sha256'] for r in ROLES},
            'llm_config':rt.llm_config.safe_metadata() if rt.llm_config else None}
        started=time.perf_counter()
        result=self.graph.invoke(state,config={'recursion_limit':50})
        # Avoid official record_decision/settlement/reflection side effects.
        result.pop('messages',None)
        result['resolved_company']=rt.company
        result['question']=rt.question
        result['model_trace']=rt.model_trace
        result['run_usage']={'seconds':round(time.perf_counter()-started,4),'llm_calls':rt.llm_calls,
            'rag_tool_invocations':rt.tool_calls,'max_llm_calls':rt.max_llm_calls,
            'news_tool_invocations':rt.news_fetch_calls,'host_tool_invocations':rt.tool_calls+rt.news_fetch_calls,
            'max_rag_tool_invocations':rt.max_tool_calls,'max_unique_evidence_chars':rt.max_total_chars,
            'unique_evidence_chars':sum(len(e.get('text','')) for k,e in result['evidence_registry'].items() if k.startswith('C_')),
            'retrieval_tool_calls':sum(p['budget_usage']['retrieval_tool_calls'] for p in rt.packet_list),
            'http_requests_attempted':sum(t.get('http_request_attempted',False) for t in rt.model_trace),
            'provider_failure_circuit_open':rt.online_failed,
            'total_model_input_chars':sum(t['input_chars'] for t in rt.model_trace),
            'tokens':{k:sum(t.get('usage',{}).get(k,0) for t in rt.model_trace)
                      for k in ('prompt_tokens','completion_tokens','total_tokens')},
            'stop_reason':'official_workflow_completed_with_evidence_gate'}
        result['data_fingerprints']=[p['fingerprints'] for p in rt.packet_list]
        json.dumps(result,allow_nan=False)
        return result


def run_archived_research_v3(ticker,question,as_of='current',*,backend='local',model_mode='controlled',
                 llm_config_path=None,use_dense=True,bad_rewrite=False,event=None,fetch_news=False):
    """Explicit archived v3 compatibility; not the normal research entry."""
    at,historical=freeze(as_of)
    cfg=None
    if model_mode=='online':
        from integration.secure_config import load_llm_config
        if not llm_config_path:raise ValueError('explicit authorized LLM config path required')
        cfg=load_llm_config(llm_config_path)
    rt=EvidenceRuntime(ticker,question,at.isoformat(),historical,backend=backend,
        use_dense=use_dense,llm_config=cfg,mode=model_mode,bad_rewrite=bad_rewrite,fetch_news=fetch_news)
    return EvidenceTradingAgentsGraph(rt).run_evidence(event)


def run_research(ticker,question,as_of='current',*,backend='local',model_mode='controlled',
                 llm_config_path=None,use_dense=True,bad_rewrite=False,event=None,fetch_news=False,
                 workflow='brief',news_mode='auto',**kwargs):
    """Production: bounded company RAG first, then industry news and review.

    Models receive a frozen shared background. Company RAG is unavailable after
    news; no archived sample announcement is substituted for missing live news.
    """
    if news_mode not in ('auto','native','legacy'):raise ValueError('news_mode must be auto/native/legacy')
    if news_mode=='native' or (news_mode=='auto' and model_mode=='online' and kwargs.get('prepared') is None):
        if kwargs.get('search_provider') is not None or event is not None:
            raise ValueError('Native route does not accept external search snapshots or an event substituted for provider search')
        from integration.dual_news import run_dual_research
        return run_dual_research(ticker,question,as_of,backend=backend,model_mode=model_mode,
            llm_config_path=llm_config_path,use_dense=use_dense,workflow=workflow,**kwargs)
    from integration.industry_pipeline import run_industry_research
    return run_industry_research(ticker,question,as_of,backend=backend,model_mode=model_mode,
        llm_config_path=llm_config_path,use_dense=use_dense,bad_rewrite=bad_rewrite,
        event=event,fetch_news=fetch_news,workflow=workflow,**kwargs)
