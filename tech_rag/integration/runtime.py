"""Read-only tool boundary. One frozen clock and one aggregate run budget."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from common import ROOT, sha
from aliases import resolve_company
from lexical import eligible
from research import ResearchEngine, ResearchBudget
from integration.governance import new_shared_state, register_packet, register_event

GOVERNANCE_FIELDS = tuple(new_shared_state('2000-01-01T00:00:00+00:00', True))

def governance_copy(state):
    return {k:deepcopy(state[k]) for k in GOVERNANCE_FIELDS}

def tool_permissions(historical):
    unavailable = {'enabled':False,'reason':'No registered point-in-time series/vintage provider; never substitute live data.'}
    return {'company_research':{'enabled':True},'source_followup':{'enabled':True},
            'archived_news':{'enabled':True,'scope':'verified local archive only'},
            'live_news':dict(unavailable) if historical else {'enabled':True,'scope':'bounded current Yahoo metadata candidates only; not verified body'},
            **{k:dict(unavailable) for k in ('macro','live_market','financial_series','prediction_market')}}

class EvidenceRuntime:
    def __init__(self, ticker, question, analysis_time, historical, *, backend='local',
                 use_dense=True, llm_config=None, mode='controlled', bad_rewrite=False,
                 max_llm_calls=12, max_tool_calls=4, max_total_chars=22000,fetch_news=False):
        self.company=resolve_company(ticker);self.ticker=self.company['research_symbol']
        self.question=question;self.analysis_time=analysis_time;self.historical=historical
        self.config=ROOT/'.env' if backend=='neo4j' else None
        if backend=='neo4j':
            from neo4j_store import probe
            from neo4j_links import validate_target
            validate_target(probe(self.config))
        self.engine=ResearchEngine(backend,self.config);self.backend=backend;self.use_dense=use_dense
        self.mode=mode;self.llm_config=llm_config;self.bad_rewrite=bad_rewrite
        self.max_llm_calls=max_llm_calls;self.max_tool_calls=max_tool_calls
        self.max_total_chars=max_total_chars;self.state={};self.tool_calls=0;self.llm_calls=0
        self.model_trace=[];self.source_ids=set();self.source_engines={};self.news_turns=0
        self.packet_list=[];self.models={};self.event_input=None
        self.online_failed=False
        self.fetch_news=not historical and (mode=='online' or fetch_news)
        self.news_fetch_calls=0;self.news_result=None

    @property
    def temporal_context(self):
        return {'analysis_time':self.analysis_time,'historical':self.historical}

    @property
    def as_of(self):
        return self.analysis_time if self.historical else 'current'

    def _citation(self, c):
        source=self.engine.tools.sources[c['source_id']]
        key='C_'+sha(json.dumps([c['source_id'],c['source_sha256'],c.get('pdf_page'),
                               c.get('paragraph'),c['char_start'],c['char_end']]))[:24]
        return {'citation_id':key,**{k:c.get(k) for k in
                ('chunk_id','source_id','source_sha256','source_url','report_period','available_at',
                 'historical_eligible','pdf_page','paragraph','char_start','char_end','text','section','table_policy')},
                **{k:source.get(k) for k in ('published_at','actual_public_at','availability_basis')},
                'title':source['title'],'round_trip_verified':True,'excerpt_truncated':False,
                'overlapping_reviewed_evidence_ids':[],
                'review_status':'raw_original_unreviewed_as_relation',
                'coordinate_system':'normalized whitespace Unicode, half-open [start,end)',
                'retrieval_role':'received_archived_event',
                'local_original':str(ROOT/source['local_path'])}

    def intake(self, state):
        shared=governance_copy(state)
        at=datetime.fromisoformat(self.analysis_time)
        desired={'MU':'MU_HBM4_2026','TSM':'TSM_COWOS','NVDA':'NVDA_BLACKWELL'}[self.ticker]
        candidates=[c for c in self.engine.chunks if c['ticker']==self.ticker and 150<len(c['text'])<=2200
                    and eligible(c,at,self.historical)]
        candidates.sort(key=lambda c:(c['source_id']!=desired, c['char_start']!=0,
                                     c.get('paragraph')!=11, c['source_id'],c['chunk_id']))
        body=None
        if self.event_input is not None:
            # Arbitrary external news is a candidate. Only an exact, locally
            # verified coordinate record can enter the evidence registry.
            target=self.engine.chunks_by_id.get(self.event_input.get('chunk_id'))
            if target and self.event_input.get('text')==target['text'] and target in candidates:
                body=target
        elif candidates:
            body=candidates[0]
        if body and self.engine.verify_chunk(body)[0]:
            c=self._citation(body)
            packet={'analysis_time':self.analysis_time,
                    'temporal_mode':'strict_historical' if self.historical else 'current_as_collected',
                    'citations':{c['citation_id']:c},'evidence_map':{},'assertion_map':{},
                    'unknowns':[],'insufficiency_reasons':[]}
            register_packet(shared,packet);self.source_ids.add(c['source_id'])
            event={'original_source_id':c['source_id'],
                   'original_event_id':c['source_id'],
                   'source_sha256':c['source_sha256'],'primary_version':c['source_sha256'],
                   'text':c['text'],'evidence_ids':[c['citation_id']],
                   'available_at':c['available_at'],'subject':self.ticker,
                   'body_acquired':True,'source_verified':True,'information_channels':['news'],
                   'valid_time':{'from':None,'to':None}}
            result=register_event(shared,event)
            news={'body_acquired':True,'source_verified':True,'citation_id':c['citation_id'],
                  'source_id':c['source_id'],'event_id':(result.get('event') or {}).get('event_id'),
                  'text':c['text'],'input_kind':'archived_source_fixture'}
        else:
            shared['unresolved_gaps'].append('Received news has no eligible verified original body; metadata is only a candidate.')
            news={'body_acquired':False,'source_verified':False,'input_kind':'candidate_metadata',
                  'metadata':self.event_input or {}}
        trace=[{'tool':'evidence_intake','analysis_time':self.analysis_time,**{k:v for k,v in news.items() if k not in ('text','metadata')}}]
        return {**shared,'received_event':news,'tool_trace':trace,
                'tool_permissions':tool_permissions(self.historical)}

    def retrieve(self, state, question, *, source_id=None, cursor=None):
        shared=governance_copy(state);started=time.perf_counter()
        if not isinstance(question,str) or not question.strip() or len(question)>4000:
            return shared,{'status':'insufficient','reason':'question must be 1..4000 characters'},None
        used=sum(len(e.get('text','')) for k,e in shared['evidence_registry'].items() if k.startswith('C_'))
        left=self.max_total_chars-used
        if self.tool_calls+self.news_fetch_calls>=self.max_tool_calls or left<500:
            return shared, {'status':'insufficient','reason':'aggregate_tool_or_text_budget',
                            'stop_reason':'budget_limit'}, None
        if source_id:
            if source_id not in self.source_ids:
                return shared,{'status':'insufficient','reason':'source was not discovered in verified evidence'},None
            if source_id not in self.source_engines:
                self.source_engines[source_id]=ResearchEngine(self.backend,self.config,
                    chunks=[c for c in self.engine.chunks if c['source_id']==source_id],
                    index_path=':memory:',tools=self.engine.tools)
            engine=self.source_engines[source_id]
        else:
            engine=self.engine
        budget={'use_dense':self.use_dense and not source_id,'max_total_chars':max(200,min(left,14000)),
                'max_results':10,'max_excerpt_chars':1800,'context_radius':1,'max_pages':3}
        if cursor:
            # A research cursor commits to its original query AND packet budget.
            # Shrinking that budget to the run's remaining text changes its key.
            # Reuse the signed budget, then enforce remaining *unique* text below.
            previous=next((p for p in reversed(state.get('evidence_packets',[]))
                if p.get('pagination',{}).get('next_cursor')==cursor and p.get('question')==question),None)
            if previous is None:
                return shared,{'status':'insufficient','reason':'cursor_not_from_same_verified_query'},None
            budget=deepcopy(previous['budget'])
        packet=engine.build(self.ticker,question,self.as_of,budget,cursor=cursor,
                            temporal_context=self.temporal_context)
        # The aggregate limit counts unique original text, including received
        # news, regardless of how many pages or tools return it.
        proposed={k:c for k,c in packet['citations'].items() if k not in shared['evidence_registry']}
        if sum(len(c['text']) for c in proposed.values())>left:
            return shared,{'status':'insufficient','reason':'aggregate_text_budget'},None
        result=register_packet(shared,packet)
        # Reconcile exact intervals across tool rounds. Two partial chunks can
        # cover one curated quote even when neither individual page does.
        combined=self.reconcile(shared)
        packet['cross_tool_assembly']=combined
        self.tool_calls+=1;self.packet_list.append(packet)
        self.source_ids.update(c['source_id'] for c in packet['citations'].values())
        summary={'status':packet['status'],'analysis_time':packet['analysis_time'],
                 'citations':{k:{a:c.get(a) for a in ('source_id','pdf_page','paragraph','text','available_at')}
                              for k,c in list(packet['citations'].items())[:5]},
                 'assertion_ids':list(shared['assertion_registry']),
                 'cross_tool_fully_covered_evidence':sum(e['fully_covered'] for e in combined['evidence_map'].values()),
                 'unknowns':packet['unknowns'],'insufficiency_reasons':packet['insufficiency_reasons'],
                 'source_ids':sorted(self.source_ids),'next_cursor':packet['pagination']['next_cursor'],
                 'registration_issues':result['issues'],
                 'seconds':round(time.perf_counter()-started,4),
                 'retrieval_tool_calls':packet['budget_usage']['retrieval_tool_calls'],
                 'text_chars':packet['budget_usage']['text_chars']}
        return shared,summary,packet

    def reconcile(self,shared):
        citations={k:{a:v for a,v in c.items() if a not in ('record_hash','registry_kind')}
                   for k,c in shared['evidence_registry'].items() if k.startswith('C_')}
        at=datetime.fromisoformat(self.analysis_time)
        for c in citations.values():
            c['overlapping_reviewed_evidence_ids']=[eid for eid in self.engine.c2e[c['chunk_id']]
                if eligible(self.engine.tools.evidence[eid],at,self.historical)
                and self.engine.tools.evidence[eid]['char_start']<c['char_end']
                and self.engine.tools.evidence[eid]['char_end']>c['char_start']]
        assembled=self.engine.assemble(citations,at,
            self.historical,ResearchBudget(max_total_chars=self.max_total_chars,use_dense=False),
            self.ticker,getattr(self.engine,'_last_graph_paths',[]))
        registration={'analysis_time':self.analysis_time,
            'temporal_mode':'strict_historical' if self.historical else 'current_as_collected',
            'citations':{},'evidence_map':assembled['evidence_map'],
            'assertion_map':assembled['assertion_map'],'unknowns':[], 'insufficiency_reasons':[]}
        result=register_packet(shared,registration)
        return {**assembled,'registration_issues':result['issues'],
                'scope':'exact union of verified C citations in shared state; no new search or model inference'}

    def build_tools(self):
        from typing import Annotated
        from langchain_core.tools import tool, InjectedToolCallId
        from langgraph.prebuilt import InjectedState
        from langgraph.types import Command
        from langchain_core.messages import ToolMessage
        @tool
        def search_news(state:Annotated[dict,InjectedState],tool_call_id:Annotated[str,InjectedToolCallId],
                        lookback_days:int=7,limit:int=5):
            """Find current company news titles/summaries/links. Unverified candidates only, never original evidence; disabled for historical queries."""
            from integration.live_news import search_live_news
            if self.historical:
                result={'status':'unsupported_historical','items':[],'reason':'Live news has no verified historical version.'}
            elif self.news_result is not None:
                result={**self.news_result,'reused_within_run':True}
            elif self.tool_calls+self.news_fetch_calls>=self.max_tool_calls:
                result={'status':'insufficient','items':[],'reason':'aggregate_tool_budget'}
            else:
                self.news_fetch_calls+=1
                try:result=search_live_news(self.ticker,self.analysis_time,False,lookback_days,limit)
                except ValueError:result={'status':'invalid_arguments','items':[]}
                self.news_result=result
            trace=[*state.get('tool_trace',[]),{'tool':'search_news','analysis_time':self.analysis_time,
                'status':result['status'],'candidate_count':len(result.get('items',[])),
                'body_verified':False,'provider':result.get('provider'),'error_type':result.get('error_type')}]
            return Command(update={'news_candidates':result.get('items',[]),'tool_trace':trace,
                'messages':[ToolMessage(content=json.dumps(result,ensure_ascii=False),tool_call_id=tool_call_id,name='search_news')]})
        def call(state,call_id,question,source_id=None,cursor=None):
            shared,summary,packet=self.retrieve(state,question,source_id=source_id,cursor=cursor)
            name='source_followup' if source_id else 'company_research'
            trace=[*state.get('tool_trace',[]),{'tool':name,'question':question,'source_id':source_id,
                   'analysis_time':self.analysis_time,**{k:summary.get(k) for k in
                   ('status','reason','seconds','retrieval_tool_calls','text_chars','next_cursor')}}]
            packets=[*state.get('evidence_packets',[]),*([packet] if packet else [])]
            return Command(update={**shared,'tool_trace':trace,'evidence_packets':packets,
                'messages':[ToolMessage(content=json.dumps(summary,ensure_ascii=False),tool_call_id=call_id,name=name)]})
        @tool
        def company_research(state:Annotated[dict,InjectedState],
                             tool_call_id:Annotated[str,InjectedToolCallId], question:str='',cursor:str=''):
            """Research company evidence. For pagination supply only the returned cursor; omit question to retain the exact frozen query. Do not rewrite a query while reusing its cursor."""
            if cursor and not question:
                previous=next((p for p in reversed(state.get('evidence_packets',[]))
                    if p.get('pagination',{}).get('next_cursor')==cursor),None)
                question=previous.get('question','') if previous else ''
            return call(state,tool_call_id,question,cursor=cursor or None)
        @tool
        def source_followup(source_id:str, question:str,state:Annotated[dict,InjectedState],
                            tool_call_id:Annotated[str,InjectedToolCallId]):
            """Search a previously verified source to address an explicit evidence gap. Same frozen clock and remaining budget."""
            return call(state,tool_call_id,question,source_id=source_id)
        self.tools=(company_research,source_followup) if self.historical else (search_news,company_research,source_followup)
        return self.tools
