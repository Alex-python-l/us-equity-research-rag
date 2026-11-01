"""A/B on the same pinned official graph, with common verified news evidence."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from common import ROOT,dump,sha,readl
from lexical import eligible
from integration.framework import (EvidenceTradingAgentsGraph,TradingAgentsGraph,
    EvidenceExtension,DEFAULT_CONFIG)
from integration.runtime import EvidenceRuntime,governance_copy,tool_permissions
from integration.governance import register_packet,register_event
from integration.effect_context import role_prompt
from integration.effect_models import EffectRoleModel
from integration.prompts import ROLES


class EffectRuntime(EvidenceRuntime):
    def __init__(self,case,arm,*,audit_dir,batch_budget=None,llm_config=None,mode='controlled',
                 backend='local',use_dense=True,engine=None,replay_source=None,replay_only=False):
        # Deliberately whitelist the public input. private_rubric is never kept
        # on the runtime, model, graph state, tool descriptions or prompts.
        self.public_case={k:deepcopy(case[k]) for k in ('id','ticker','question','as_of','availability_mode','news_input')}
        self.arm=arm;self.effect_run_id=case['id']+'_'+arm
        super().__init__(case['ticker'],case['question'],case['as_of'],case['availability_mode']=='strict_historical',
                         backend=backend,use_dense=use_dense,llm_config=llm_config,mode=mode,
                         max_llm_calls=12,max_tool_calls=2,max_total_chars=22000,fetch_news=False)
        if engine is not None:self.engine=engine
        self.fetch_news=False # frozen paired archive: never use automatic current news
        self.audit_dir=Path(audit_dir);self.audit_dir.mkdir(parents=True,exist_ok=True)
        self.batch_budget=batch_budget
        self.replay_source=Path(replay_source) if replay_source else None
        self.replay_only=replay_only

    def build_tools(self):
        if self.arm=='A':self.tools=();return self.tools
        super().build_tools()
        self.tools=tuple(t for t in self.tools if t.name in {'company_research','source_followup'})
        return self.tools

    def retrieve(self,state,question,*,source_id=None,cursor=None):
        shared,summary,packet=super().retrieve(state,question,source_id=source_id,cursor=cursor)
        if packet:
            from effect_v4_20261018.retrieval.diagnose_dev import capture_funnel
            engine=self.source_engines.get(source_id,self.engine) if source_id else self.engine
            snapshot=capture_funnel(engine,packet)
            name=f'retrieval_{self.tool_calls:02d}_candidates.json'
            dump(self.audit_dir/name,snapshot)
            summary['candidate_audit_file']=str(self.audit_dir/name)
        return shared,summary,packet

    def intake(self,state):
        shared=governance_copy(state);news=self.public_case['news_input']
        citations={};at=datetime.fromisoformat(self.analysis_time)
        source=self.engine.tools.sources[news['source_id']]
        blocks=readl(ROOT/source['text_path'])
        for row in news['paragraphs']:
            anchor=row['anchor'];block=next(b for b in blocks if b.get('pdf_page')==anchor.get('pdf_page')
                                          and b.get('paragraph')==anchor.get('paragraph'))
            if sha(block['text'])!=anchor['text_block_sha256']:raise ValueError('frozen news block changed')
            normalized=re.sub(r'\s+',' ',block['text']).strip()
            if re.sub(r'\s+',' ',row['text']).strip()!=normalized:raise ValueError('news paragraph is incomplete or changed')
            existing=next((c for c in self.engine.chunks if c['source_id']==news['source_id']
                         and c.get('pdf_page')==anchor.get('pdf_page') and c.get('paragraph')==anchor.get('paragraph')
                         and c['char_start']==0 and c['char_end']==len(normalized)),None)
            c=deepcopy(existing) if existing else {
                'chunk_id':'LOC4_'+sha(news['source_id']+str(anchor.get('pdf_page'))+str(anchor.get('paragraph'))+normalized)[:22],
                'source_id':news['source_id'],'source_sha256':source['sha256'],'source_url':source['url'],
                'ticker':self.ticker,'pdf_page':anchor.get('pdf_page'),'paragraph':anchor.get('paragraph'),
                'char_start':0,'char_end':len(normalized),'text':normalized,'section':'Frozen complete original paragraph',
                'available_at':source['available_at'],'historical_eligible':source['historical_eligible'],
                'report_period':source.get('report_period'),'table_policy':block.get('table_policy')}
            if not eligible(c,at,self.historical):raise ValueError('news unavailable at frozen time')
            valid,why=self.engine.verify_chunk(c)
            if not valid:raise ValueError('news original verification failed: '+str(why))
            cit=self._citation(c);citations[cit['citation_id']]=cit
            # Exact synthetic locator records may span normal sentence chunks.
            # Link only overlapping evidence in the very same source/version/block.
            if not existing:
                self.engine.c2e[c['chunk_id']]=[eid for eid,e in self.engine.tools.evidence.items()
                    if e['source_id']==c['source_id'] and e.get('pdf_page')==c.get('pdf_page')
                    and e.get('paragraph')==c.get('paragraph') and e.get('char_start',0)<c['char_end']
                    and e.get('char_end',0)>c['char_start']]
        result=register_packet(shared,{'analysis_time':self.analysis_time,
                'temporal_mode':'strict_historical' if self.historical else 'current_as_collected',
                'citations':citations,'evidence_map':{},'assertion_map':{},'unknowns':[],'insufficiency_reasons':[]})
        if result['issues']:raise ValueError('news packet registration failed')
        register_event(shared,{'original_source_id':news['source_id'],'original_event_id':news['source_id'],
            'source_sha256':source['sha256'],'primary_version':source['sha256'],
            'text':news['text'],'evidence_ids':list(citations),'available_at':source['available_at'],
            'subject':self.ticker,'body_acquired':True,'source_verified':True,
            'information_channels':['news'],'valid_time':{'from':None,'to':None}})
        self.source_ids.add(news['source_id'])
        perms=tool_permissions(self.historical)
        perms['live_news']={'enabled':False,'reason':'fixed paired news snapshot; no network news calls'}
        if self.arm=='A':
            for key in ('company_research','source_followup'):perms[key]={'enabled':False,'reason':'no-RAG experimental arm'}
        return {**shared,'received_event':{'source_id':news['source_id'],'source_sha256':source['sha256'],
            'citation_ids':list(citations),'body_acquired':True,'source_verified':True,
            'snapshot_kind':news['snapshot_kind'],'is_full_article':False,
            'input_kind':'verified_complete_original_paragraph_excerpt','news_text_sha256':sha(news['text'])},
            'tool_trace':[{'tool':'evidence_intake','source_id':news['source_id'],
                           'analysis_time':self.analysis_time,'body_verified':True,'citation_ids':list(citations)}],
            'tool_permissions':perms}


class EffectExtension(EvidenceExtension):
    def __init__(self,runtime):
        self.runtime=runtime;runtime.build_tools()
        runtime.models={role:EffectRoleModel(runtime,role) for role in ROLES}

    def wrap_node(self,role,official_node):
        wrapped=super().wrap_node(role,official_node)
        def node(state):
            update=wrapped(state)
            update['role_trace'][-1]['prompt_sha256']=role_prompt(role)['sha256']
            return update
        return node


class EffectGraph(EvidenceTradingAgentsGraph):
    def __init__(self,runtime):
        self.evidence_runtime=runtime;extension=EffectExtension(runtime)
        cfg=deepcopy(DEFAULT_CONFIG)
        cfg.update(results_dir=str(runtime.audit_dir/'official_unused_results'),
            data_cache_dir=str(ROOT/'state/framework_cache'),memory_log_path=str(ROOT/'state/framework_disabled_memory.txt'),
            max_debate_rounds=1,max_risk_discuss_rounds=1,max_tool_rounds=2,max_recur_limit=50,
            checkpoint_enabled=False,llm_max_retries=0)
        TradingAgentsGraph.__init__(self,selected_analysts=['news'],config=cfg,
            model_bundle={'quick':runtime.models['news'],'deep':runtime.models['research_manager']},
            evidence_extension=extension)

    def run_evidence(self,event=None):
        result=super().run_evidence(event)
        rt=self.evidence_runtime
        result['run_id']=rt.effect_run_id
        result['framework_metadata'].update(experiment='effect_v4_blank_schema',arm=rt.arm,
            prompt_hashes={r:role_prompt(r)['sha256'] for r in ROLES},
            claim_template_used=False,live_news_enabled=False,
            fixed_no_signal_is_quality_metric=False,news_text_sha256=sha(rt.public_case['news_input']['text']))
        result['run_usage']['stop_reason']=('provider_or_budget_incomplete' if rt.online_failed or any(
            t.get('status') in {'budget_stop','required_context_budget'} for t in rt.model_trace)
            else 'official_workflow_completed_with_evidence_gate')
        attempted=[t for t in rt.model_trace if t.get('http_request_attempted')]
        known=all(type(t.get('usage',{}).get('total_tokens')) is int for t in attempted)
        result['run_usage']['provider_usage_known']=known
        result['run_usage']['observed_token_lower_bound']=deepcopy(result['run_usage']['tokens'])
        if not known:result['run_usage']['tokens']={k:None for k in ('prompt_tokens','completion_tokens','total_tokens')}
        result['run_usage']['real_roles_with_provider_response']=sorted({t['role'] for t in attempted if t.get('status')=='provider_ok'})
        result['run_usage']['format_failures']=sum(bool(t.get('format_failure')) for t in rt.model_trace)
        result['run_usage']['output_truncations']=sum(bool(t.get('output_truncated')) for t in rt.model_trace)
        result['model_context_index']=[{k:t.get(k) for k in ('role','input_file','messages_sha256',
            'context_evidence_ids','omitted_optional_evidence_ids')} for t in rt.model_trace]
        dump(rt.audit_dir/'result.json',result)
        return result
