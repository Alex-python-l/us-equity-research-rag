"""Bounded role models used by the official factories; no hidden retries."""
from copy import deepcopy
import json
import re
import time
import uuid

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from common import sha
from integration.prompts import load_prompt
from integration.governance import eligible_claims
from integration.context import select_evidence,terms


def latest_accepted(state):
    return eligible_claims(state)


def claim_template(runtime,role):
    state=runtime.state;parents=latest_accepted(state)
    if parents:
        parent=parents[-1];c={k:deepcopy(parent[k]) for k in
            ('text','type','subject','product','business_scope','evidence_ids','conditions',
             'unknowns','available_at','valid_time','impact_target','horizon')}
        c.update(parent_claim_ids=[parent['claim_id']],type='conditional_hypothesis',
                 text='If the disclosed scope and inherited conditions hold, the business delivery impact may change; the sign and magnitude remain uncertain.')
        if 'Actual demand, inventory buffers, alternative supply and product qualification must be verified.' not in c['conditions']:
            c['conditions'].append('Actual demand, inventory buffers, alternative supply and product qualification must be verified.')
    else:
        assertions=[a for a in state.get('assertion_registry',{}).values()
                    if a.get('subject_id')==runtime.ticker]
        requested=terms(runtime.question)
        assertions.sort(key=lambda a:(-len(terms(a['claim']+' '+a['business_scope'])&requested),a['relation_id']))
        if assertions:
            a=assertions[0]
            c={'text':a['claim'],'type':'original_fact','subject':a['subject_id'],'product':'unspecified',
               'business_scope':a['business_scope'],'evidence_ids':a['evidence_ids'],
               'parent_claim_ids':[],'conditions':[a['conditions']] if a.get('conditions') else [],
               'unknowns':[],'available_at':a['available_at'],
               'valid_time':{'from':a.get('valid_from'),'to':a.get('valid_to')},
               'impact_target':'delivery','horizon':'disclosure scope; no return forecast'}
        else:
            evidence=next((e for e in state.get('evidence_registry',{}).values()
                if e.get('registry_kind')=='exact_original_quote' and
                re.search({'MU':'micron','TSM':'tsmc|taiwan semiconductor','NVDA':'nvidia'}[runtime.ticker],e['text'],re.I)),None)
            if not evidence:return None
            c={'text':evidence['text'],'type':'original_fact','subject':runtime.ticker,
               'product':'unspecified','business_scope':'unspecified',
               'evidence_ids':[evidence['citation_id']],'parent_claim_ids':[],
               'conditions':[],'unknowns':[],'available_at':evidence['available_at'],
               'valid_time':{'from':None,'to':None},'impact_target':'delivery',
               'horizon':'disclosure scope; no return forecast'}
    # These are tool-supplied evidence limitations, not automatically invented
    # model assumptions. Online models receive this exact proposed schema too.
    for eid in c['evidence_ids']:
        for gap in state['evidence_registry'][eid].get('packet_unknowns',[]):
            if gap not in c['unknowns']:c['unknowns'].append(gap)
    for gap in state.get('unresolved_gaps',[]):
        if gap not in c['unknowns']:c['unknowns'].append(gap)
    c.update(claim_id=role+'_claim',analysis_time=runtime.analysis_time,status='proposed')
    return c


class RoleModel(Runnable):
    def __init__(self,runtime,role,bound_tools=()):
        self.runtime=runtime;self.role=role;self.bound_tools=tuple(bound_tools)
        self.last_payload=None;self.last_raw=''

    def bind_tools(self,tools,**kwargs):
        # The upstream news factory names live tools. They are replaced by the
        # explicit research allowlist, including in the bound model schema.
        return self.runtime.models[self.role]._bound()

    def _bound(self):
        return RoleModel(self.runtime,self.role,self.runtime.tools)

    def with_structured_output(self,*args,**kwargs):
        # Use the official documented free-text fallback exactly once, then
        # validate our JSON contract. No failed schema call + paid retry.
        raise NotImplementedError('Evidence JSON is independently validated')

    def _news_schedule(self):
        rt=self.runtime
        return (['search_news'] if rt.fetch_news else [])+['company_research']+(['source_followup'] if rt.source_ids else [])

    def invoke(self,input,config=None,**kwargs):
        rt=self.runtime;model=rt.models[self.role]
        prompt=load_prompt(self.role)
        if rt.mode=='online' and rt.online_failed:
            model.last_payload={'report':'needs_more_evidence: provider failure circuit is open; no retry','claims':[]}
            model.last_raw=json.dumps(model.last_payload)
            return AIMessage(content=model.last_raw)
        if rt.llm_calls>=rt.max_llm_calls:
            payload={'report':'needs_more_evidence: model call budget exhausted','claims':[]}
            response=AIMessage(content=json.dumps(payload))
            model.last_payload=payload;model.last_raw=response.content
            return response
        rt.llm_calls+=1;started=time.perf_counter()
        suggested=claim_template(rt,self.role)
        if hasattr(input,'to_messages'):
            upstream='\n'.join(str(m.content) for m in input.to_messages())
        else:upstream=str(input)
        # The full state is saved in the run. Only bounded, identified evidence
        # and complete inheritance fields enter each model context.
        selected=select_evidence(rt.state.get('evidence_registry',{}),rt.question,
                                required=(suggested or {}).get('evidence_ids',[]),limit=6)
        state_view={'analysis_time':rt.analysis_time,'historical':rt.historical,
            'company':rt.company,'question':rt.question,
            'received_event':{k:v for k,v in rt.state.get('received_event',{}).items() if k!='text'},
            'claim_template':suggested,'unresolved_gaps':rt.state.get('unresolved_gaps',[]),
            'accepted_claims':latest_accepted(rt.state)[-1:],
            'recent_validation_feedback':[{k:i.get(k) for k in ('code','claim_id','severity')} for i in rt.state.get('review_issues',[])[-6:]],
            'assertions':list(rt.state.get('assertion_registry',{}).values())[:3],
            'evidence':{k:{a:v for a,v in e.items() if a in
              ('source_id','source_sha256','source_url','available_at','text','packet_unknowns','citation_ids')}
              for k,e in selected.items()},
            'source_ids':sorted(rt.source_ids),'tool_permissions':rt.state.get('tool_permissions',{})}
        state_view['news_candidates_unverified']=[{k:c.get(k) for k in
            ('candidate_id','title','url','publisher','provider_published_at','status')}
            for c in rt.state.get('news_candidates',[])[:5]]
        from integration.scope_context import qualify_record
        for eid,value in state_view['evidence'].items():
            value['publisher_scope']=qualify_record(rt.state['evidence_registry'][eid],rt.analysis_time,rt.historical)
        trusted=json.dumps(state_view,ensure_ascii=False)
        # No truncation inside the machine inheritance schema. Remove optional
        # excerpts first; the required template is always preserved.
        if len(prompt['text'])+len(trusted)+120>18000:
            state_view['news_candidates_unverified']=[]
            state_view['assertions']=[];state_view['accepted_claims']=[]
            trusted=json.dumps(state_view,ensure_ascii=False)
        required=set((suggested or {}).get('evidence_ids',[]))
        while len(prompt['text'])+len(trusted)+120>18000:
            optional=[k for k in state_view['evidence'] if k not in required]
            if not optional:break
            state_view['evidence'].pop(optional[-1])
            trusted=json.dumps(state_view,ensure_ascii=False)
        if len(prompt['text'])+len(trusted)+120>18000:
            model.last_payload={'report':'needs_more_evidence: required inheritance exceeds model context budget','claims':[]}
            model.last_raw=json.dumps(model.last_payload)
            rt.model_trace.append({'role':self.role,'mode':rt.mode,'analysis_time':rt.analysis_time,
                'prompt_sha256':prompt['sha256'],'input_chars':0,'output_chars':len(model.last_raw),
                'status':'required_context_budget','seconds':round(time.perf_counter()-started,4)})
            return AIMessage(content=model.last_raw)
        room=max(0,18000-len(prompt['text'])-len(trusted)-120)
        user='TRUSTED_STATE\n'+trusted+'\nUNTRUSTED_LEGACY_ROLE_DATA\n'+(upstream[-room:] if room else '')
        messages=[{'role':'system','content':prompt['text']},{'role':'user','content':user}]
        trace={'role':self.role,'mode':rt.mode,'analysis_time':rt.analysis_time,
               'prompt_sha256':prompt['sha256'],'input_chars':sum(len(m['content']) for m in messages),
               'legacy_truncated':len(upstream)>room,'tools':[t.name for t in self.bound_tools]}
        try:
            if rt.mode=='controlled':
                response=self._controlled(suggested)
            elif rt.mode=='online':
                response=self._online(messages,trace)
            else:raise ValueError('mode must be controlled or online')
        except Exception as exc:
            # Never log response headers, bearer tokens, request repr or a raw
            # provider exception, which may include sensitive request details.
            trace.update(error_type=type(exc).__name__,status='model_call_failed')
            if rt.mode=='online':rt.online_failed=True
            response=AIMessage(content=json.dumps({'report':'needs_more_evidence: bounded model call failed','claims':[]}))
        trace['seconds']=round(time.perf_counter()-started,4)
        trace['output_chars']=len(response.content or '')
        rt.model_trace.append(trace)
        model.last_raw=response.content or ''
        try:
            raw=model.last_raw.strip()
            if raw.startswith('```'):raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw)
            model.last_payload=json.loads(raw) if raw else None
        except (ValueError,TypeError):model.last_payload=None
        return response

    def _controlled(self,claim):
        rt=self.runtime
        if self.role=='news' and self.bound_tools:
            schedule=self._news_schedule();index=rt.news_turns;rt.news_turns+=1
            if index<len(schedule):
                name=schedule[index]
                if name=='search_news':args={'lookback_days':7,'limit':5}
                elif name=='company_research':args={'question':rt.question}
                else:
                    preferred=rt.state.get('received_event',{}).get('source_id')
                    source=preferred if preferred in rt.source_ids else sorted(rt.source_ids)[0]
                    args={'source_id':source,'question':rt.question+' conditions qualification capacity unnamed customers'}
                return AIMessage(content='',tool_calls=[{'name':name,'args':args,
                    'id':'tool_'+uuid.uuid4().hex[:12],'type':'tool_call'}])
        if rt.bad_rewrite and self.role=='trader' and claim:
            claim=deepcopy(claim)
            claim.update(type='original_fact',text='The stock price will certainly rise.',
                         conditions=[],unknowns=[],impact_target='stock_return')
        payload={'report':'Controlled model substitute; conditional research only. No stock signal.',
                 'claims':[claim] if claim else []}
        return AIMessage(content=json.dumps(payload,ensure_ascii=False))

    def _online(self,messages,trace):
        import requests
        rt=self.runtime;cfg=rt.llm_config
        if cfg is None:raise ValueError('explicit LLM config required')
        payload={'model':cfg.model,'messages':messages,'max_tokens':1800,'temperature':0,
                 'thinking':{'type':'disabled'},'stream':False}
        if self.bound_tools:
            payload['tools']=[convert_to_openai_tool(t) for t in self.bound_tools]
            payload['parallel_tool_calls']=False
            # Current research discovers news, then gets verified background.
            # Historical research never receives a live news tool.
            schedule=self._news_schedule()
            if rt.news_turns<len(schedule):
                payload['tool_choice']={'type':'function','function':{'name':schedule[rt.news_turns]}}
            else:payload['tool_choice']='none'
            rt.news_turns+=1
        trace['http_request_attempted']=True
        response=requests.post(cfg.base_url.rstrip('/')+'/chat/completions',
                headers={'Authorization':'Bearer '+cfg.api_key},json=payload,timeout=(10,45))
        trace['http_status']=response.status_code
        if response.status_code!=200:
            raise RuntimeError('provider returned non-200')
        result=response.json();trace['usage']=result.get('usage',{})
        trace['finish_reason']=result['choices'][0].get('finish_reason')
        trace['response_model']=result.get('model')
        trace['provider_system_fingerprint']=result.get('system_fingerprint')
        message=result['choices'][0]['message'];calls=message.get('tool_calls',[])
        if len(calls)>1:
            trace['rejected_parallel_calls']=len(calls)-1;calls=calls[:1]
        return AIMessage(content=message.get('content') or '',tool_calls=[
            {'name':c['function']['name'],'args':json.loads(c['function']['arguments']),
             'id':c['id'],'type':'tool_call'} for c in calls
             if c['function']['name'] in {t.name for t in self.bound_tools}])
