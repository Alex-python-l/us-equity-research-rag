"""Real provider calls for the blank-schema effect experiment (no retry).

Every exact, credential-free request body and provider result is archived.
The budget ledger is independent of the graph and survives interruption.
"""
from copy import deepcopy
import json
import re
import time
import uuid

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from common import dump,sha,load
from integration.governance import eligible_claims
from integration.models import RoleModel
from integration.effect_context import build_model_context,role_prompt,ContextBudget
from integration.effect_cost import BudgetStop


def recorded_response(input_file,provider_file,payload):
    """Use a previous paid response only when the ENTIRE request is identical."""
    previous=load(input_file)
    if previous['request']!=payload:raise BudgetStop('recorded request differs; refusing replay or new paid retry')
    return previous['request_id'],load(provider_file)


class EffectRoleModel(RoleModel):
    @property
    def prompt_sha256(self):return role_prompt(self.role)['sha256']

    def _bound(self):return EffectRoleModel(self.runtime,self.role,self.runtime.tools)

    def invoke(self,input,config=None,**kwargs):
        rt=self.runtime;model=rt.models[self.role];started=time.perf_counter()
        trace=dict(role=self.role,mode=rt.mode,analysis_time=rt.analysis_time,
                   prompt_sha256=self.prompt_sha256,http_request_attempted=False,input_chars=0)
        response=None
        try:
            if rt.online_failed:raise BudgetStop('provider failure circuit open')
            ctx=build_model_context(rt.state,self.role,rt.ticker,rt.question,rt.analysis_time,
                rt.historical,eligible_claims(rt.state),max_chars=18000,required_max_chars=32000,retrieval_budget={
                    'max_tools':rt.max_tool_calls,'remaining_tools':max(0,rt.max_tool_calls-rt.tool_calls),
                    'max_unique_text_chars':rt.max_total_chars,
                    'remaining_unique_text_chars':max(0,rt.max_total_chars-sum(len(e.get('text',''))
                        for eid,e in rt.state.get('evidence_registry',{}).items() if eid.startswith('C_')))})
            payload={'model':rt.llm_config.model if rt.llm_config else 'controlled-substitute',
                     'messages':ctx['messages'],'max_tokens':1800,'temperature':0,
                     'thinking':{'type':'disabled'},'stream':False}
            if self.bound_tools:
                payload['tools']=[convert_to_openai_tool(t) for t in self.bound_tools]
                payload['parallel_tool_calls']=False
                payload['tool_choice']=({'type':'function','function':{'name':'company_research'}}
                                       if rt.news_turns==0 else 'auto' if rt.news_turns<2 else 'none')
            # Equal JSON instructions and empty output schema in both arms;
            # neither arm gets an extra provider-enforced answer schema.
            trace.update({k:ctx[k] for k in ('input_chars','messages_sha256','context_evidence_ids',
                         'required_evidence_ids','omitted_optional_evidence_ids','whole_evidence_only')})
            if rt.mode=='controlled':
                number=len(rt.model_trace)+1
                stem=rt.audit_dir/f'{number:02d}_{self.role}'
                dump(str(stem)+'_input.json',{'request':payload,'context_audit':{k:v for k,v in ctx.items() if k!='messages'}})
                trace['input_file']=str(stem)+'_input.json'
                if self.role=='news' and self.bound_tools and rt.news_turns<2:
                    name='company_research' if rt.news_turns==0 else 'source_followup'
                    args={'question':rt.question}
                    if name=='source_followup':args['source_id']=sorted(rt.source_ids)[0]
                    response=AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':'ct4_'+uuid.uuid4().hex[:10],'type':'tool_call'}])
                else:
                    response=AIMessage(content=json.dumps({'report':'Controlled contract check only; no quality assessment.',
                        'claims':[],'answer_status':'insufficient','unknowns':['Model substitute; not an answer.']}))
                trace['status']='controlled_substitute'
            else:
                number=rt.llm_calls+1;stem=rt.audit_dir/f'{number:02d}_{self.role}'
                old_stem=rt.replay_source/f'{number:02d}_{self.role}' if rt.replay_source else None
                if old_stem and __import__('pathlib').Path(str(old_stem)+'_provider.json').exists():
                    rid,result=recorded_response(str(old_stem)+'_input.json',str(old_stem)+'_provider.json',payload)
                    rt.llm_calls+=1
                    trace.update(request_id=rid,input_file=str(old_stem)+'_input.json',response_file=str(old_stem)+'_provider.json',
                        request_sha256=sha(json.dumps(payload,ensure_ascii=False,sort_keys=True)),http_request_attempted=True,
                        new_http_request_in_this_process=False,response_replayed_without_new_request=True,
                        usage=result.get('usage',{}),status='provider_ok',response_model=result.get('model'),
                        provider_system_fingerprint=result.get('system_fingerprint'))
                    response=self._decode(result,trace)
                else:
                    if rt.replay_only:raise BudgetStop('recorded replay incomplete; no provider request allowed')
                    rid=rt.batch_budget.reserve(rt.effect_run_id,self.role);rt.llm_calls+=1
                    trace.update(request_id=rid,input_file=str(stem)+'_input.json',request_sha256=sha(json.dumps(payload,ensure_ascii=False,sort_keys=True)),
                                 new_http_request_in_this_process=True)
                    dump(trace['input_file'],{'request_id':rid,'request':payload,
                        'context_audit':{k:v for k,v in ctx.items() if k!='messages'}})
                    response=self._request(payload,trace,rid,stem)
            if self.role=='news':rt.news_turns+=1
        except (BudgetStop,ContextBudget) as exc:
            trace.update(status='budget_stop' if isinstance(exc,BudgetStop) else 'required_context_budget',stop_reason=str(exc))
            response=AIMessage(content=json.dumps({'report':'insufficient: '+str(exc),'claims':[],
                                'answer_status':'insufficient','unknowns':[str(exc)]}))
        except Exception as exc:
            trace.update(status='model_call_failed',error_type=type(exc).__name__)
            rt.online_failed=rt.mode=='online'
            response=AIMessage(content=json.dumps({'report':'insufficient: bounded model call failed',
                'claims':[],'answer_status':'insufficient','unknowns':['Provider or format failure; see audit.']}))
        trace.update(seconds=round(time.perf_counter()-started,4),output_chars=len(response.content or ''))
        rt.model_trace.append(trace);model.last_raw=response.content or ''
        try:
            raw=re.sub(r'^```(?:json)?\s*|\s*```$','',model.last_raw.strip())
            model.last_payload=json.loads(raw) if raw else None
            if not response.tool_calls and not isinstance(model.last_payload,dict):trace['format_failure']=True
        except (ValueError,TypeError):
            model.last_payload=None;trace['format_failure']=True
        return response

    def _request(self,payload,trace,rid,stem):
        import requests
        rt=self.runtime;cfg=rt.llm_config
        trace['http_request_attempted']=True
        try:
            response=requests.post(cfg.base_url.rstrip('/')+'/chat/completions',
                headers={'Authorization':'Bearer '+cfg.api_key},json=payload,timeout=(10,45))
        except Exception as exc:
            rt.batch_budget.complete(rid,None,'network_failure',error_type=type(exc).__name__)
            raise RuntimeError('provider network failure') from None
        trace['http_status']=response.status_code
        if response.status_code!=200:
            rt.batch_budget.complete(rid,None,'provider_failure',http_status=response.status_code)
            raise RuntimeError('provider non-200')
        try:result=response.json()
        except ValueError:
            rt.batch_budget.complete(rid,None,'provider_format_failure',http_status=response.status_code)
            raise RuntimeError('provider response is not JSON') from None
        usage=result.get('usage',{})
        rt.batch_budget.complete(rid,usage,'ok',http_status=200,response_model=result.get('model'))
        trace.update(usage=usage,response_file=str(stem)+'_provider.json',response_model=result.get('model'),
                     provider_system_fingerprint=result.get('system_fingerprint'),status='provider_ok')
        dump(str(stem)+'_provider.json',result)
        return self._decode(result,trace)

    def _decode(self,result,trace):
        choice=result['choices'][0];trace['finish_reason']=choice.get('finish_reason')
        trace['output_truncated']=choice.get('finish_reason')=='length'
        msg=choice['message'];calls=msg.get('tool_calls',[])
        if len(calls)>1:trace['rejected_parallel_calls']=len(calls)-1
        allowed={t.name for t in self.bound_tools};safe=[]
        for call in calls[:1]:
            if call['function']['name'] in allowed:
                safe.append({'name':call['function']['name'],'args':json.loads(call['function']['arguments']),
                             'id':call['id'],'type':'tool_call'})
            else:trace.setdefault('unavailable_tool_requests',[]).append(call['function']['name'])
        return AIMessage(content=msg.get('content') or '',tool_calls=safe)
