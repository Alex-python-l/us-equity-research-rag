"""Optional official TradingAgents graph over one frozen industry snapshot.

The small v5 A/B uses the two-role default. This explicit adapter retains the
official nine factories without restoring post-news company RAG or live tools.
No additional online multi-agent experiment is implied by a controlled test.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

from common import ROOT,dump
from integration.bootstrap import bootstrap,UPSTREAM_COMMIT
TradingAgentsGraph=bootstrap()
from tradingagents.agents.state import AgentState
from tradingagents.default_config import DEFAULT_CONFIG
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from integration.prompts import ROLES
from integration.framework import EvidenceExtension
from integration.industry_pipeline import audit_findings


class FrontState(AgentState):
    industry_outputs:list
    industry_prepared:dict
    role_trace:list
    reviewed_output:dict
    research_gate:dict


class PreparedRole(Runnable):
    def __init__(self,extension,role):self.extension=extension;self.role=role;self.last={}
    def bind_tools(self,*args,**kwargs):return self
    def with_structured_output(self,*args,**kwargs):raise NotImplementedError('One JSON call; no schema retry')
    def invoke(self,input,config=None,**kwargs):
        state=self.extension.state
        previous=(state.get('industry_outputs') or [])[-1:]
        parent=previous[0]['payload'] if previous else None
        # Every factory gets the exact same selected source snapshot. Only its
        # immediate parent's complete findings are added, never unvintaged memory.
        role='reviewer' if self.role=='portfolio_manager' else self.role
        self.last=self.extension.models.invoke(role,parent)
        return AIMessage(content=json.dumps(self.last,ensure_ascii=False))


class FrontExtension:
    state_schema=FrontState
    output_fields=FrontState.__annotations__
    def __init__(self,prepared,models):
        self.prepared=prepared;self.models=models;self.state={}
        self.role_models={r:PreparedRole(self,r) for r in ROLES}
    def model_for(self,role,default):return self.role_models[role]
    def tools_for(self,key):return ()
    def intake(self,state):return {'industry_prepared':self.prepared}
    def wrap_node(self,role,official):
        def node(state):
            self.state=state
            update=official(state);payload=self.role_models[role].last
            outputs=[*state.get('industry_outputs',[]),dict(role=role,payload=payload)]
            safe='unreviewed_research: '+json.dumps(payload,ensure_ascii=False)
            if role=='portfolio_manager':
                parent=(state.get('industry_outputs') or [{}])[-1].get('payload',{})
                reviewed=audit_findings(parent,payload,self.models.delivered_news,
                                         self.models.delivered_background)
                update['reviewed_output']=reviewed
                safe=json.dumps(reviewed,ensure_ascii=False)
            EvidenceExtension._sanitize(role,state,update,safe)
            update.update(industry_outputs=outputs,role_trace=[*state.get('role_trace',[]),
                {'role':role,'factory':'official TradingAgents','new_company_rag_calls':0,
                 'background_fingerprint':self.models.context_sha256}])
            return update
        return node
    def final_gate(self,state):
        return {'research_gate':{'execution_allowed':False,
            'scope':'research-only permission; not an effectiveness score'}}


def run_multiagent(prepared,models,directory,run_id):
    extension=FrontExtension(prepared,models);cfg=deepcopy(DEFAULT_CONFIG)
    cfg.update(results_dir=str(directory/'official_unused_results'),max_debate_rounds=1,
        data_cache_dir=str(ROOT/'state/framework_cache'),
        memory_log_path=str(ROOT/'state/framework_disabled_memory.txt'),
        max_risk_discuss_rounds=1,max_tool_rounds=0,max_recur_limit=50,
        checkpoint_enabled=False,llm_max_retries=0)
    graph=TradingAgentsGraph(selected_analysts=['news'],config=cfg,
        model_bundle={'quick':extension.role_models['news'],'deep':extension.role_models['research_manager']},
        evidence_extension=extension)
    state=graph.propagator.create_initial_state(prepared['ticker'],prepared['analysis_time'][:10],
        instrument_context=json.dumps(prepared['company']),past_context='No outcome memory or live market inputs.')
    state.update(industry_prepared=prepared,industry_outputs=[],role_trace=[],
                 reviewed_output={},research_gate={})
    result=graph.graph.invoke(state,config={'recursion_limit':50})
    result.pop('messages',None)
    result.update(schema_version='industry_research_multiagent_v5',run_id=run_id,
        ticker=prepared['ticker'],question=prepared['question'],analysis_time=prepared['analysis_time'],
        historical=prepared['historical'],prepared=prepared,available_model_tools=[],
        company_rag_calls_after_news=0,model_trace=models.traces,run_usage=models.usage(),
        framework_metadata={'repository':'https://github.com/TauricResearch/TradingAgents',
            'upstream_commit':UPSTREAM_COMMIT,'model_mode':models.mode,
            'execution':'official graph and factories with frozen frontloaded evidence; no research tools'})
    dump(directory/'result.json',result);models.finish('completed' if not models.failed else 'incomplete')
    return result
