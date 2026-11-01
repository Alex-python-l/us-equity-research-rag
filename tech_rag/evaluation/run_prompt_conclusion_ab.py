"""Explicitly authorized one-round conclusion A/B; not the prior Yahoo test."""
import argparse,json,sys,time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,dump
from monitor_planner import MonitorPlanner
from integration.prompt_web_research import ExplicitABBudget,make_case,call_native,discuss_case
from integration.effect_cost import BudgetStop
from integration.news_body import fetch_article

def emit(x):print(json.dumps(x,ensure_ascii=False),flush=True)

def run(directory,*,no_token_limit=False):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    if (directory/'PROTOCOL.json').exists():raise RuntimeError('This batch already exists; never rerun automatically.')
    auth='USER_20261022_PROMPT_AB_NO_TOKEN_LIMIT' if no_token_limit else 'USER_20261022_PROMPT_AB'
    ledger_path=ROOT/'native_news_v6_20261020/budget.json'
    authorization=ExplicitABBudget.authorize(ledger_path,authorization_id=auth,
        user_statement=('用户新指示：token的用量预算不要有，真实检测一下这个系统；本次重新完成NVDA、MU的A/B四组，保留旧未知费用和失败记录。' if no_token_limit else
            '用户明确回复：授权本次有限实跑并保留未知费用记录；最多4次原生联网＋8次讨论终审；任一新用量未知即停止，不自动重试。'),
        case_ids=['NVDA_A','NVDA_B','MU_A','MU_B'],token_limit_disabled=no_token_limit)
    ledger=ExplicitABBudget(ledger_path,authorization)
    planner=MonitorPlanner();guides={t:planner.guide(t) for t in ('NVDA','MU')}
    at=datetime.now(timezone.utc).isoformat()
    cases={t+'_'+a:make_case(t,at,a,guides[t] if a=='B' else None) for t in guides for a in ('A','B')}
    protocol=dict(version='prompt_to_native_to_debate_final_ab_v2',judged_at=at,
        authorization_id=auth,authoritative_ledger=str(ledger_path),
        comparison='A: no RAG; B: exact RAG agent_prompt. Same native model, search instructions, period, output budgets and discussion/reviewer models.',
        native_model='deepseek-v4-pro',discussion_model='existing configured Flash',cases=cases,
        order=['NVDA_A','NVDA_B','MU_B','MU_A'],max_native_requests=4,max_discussion_requests=8,
        token_limit_disabled=no_token_limit,explicit_output_token_caps=False,
        native_internal_search_limit='soft prompt budget only; actual web_search_call count audited',
        previous_yahoo_experiment='different retrieval-only experiment; not evidence of conclusion-level RAG effectiveness',
        evaluation_dimensions=['结论是否因证据改变','关键影响路径','支持/反对论证和分歧','新增事实与事前预期区分','不确定性和夸大断言','来源可回查'],
        limitations=['单轮无法统计归因','两组自主检索来源可以不同：这是RAG总体研究效果，不是固定证据包推理实验',
            '原先未知费用保留，总费用未知；已知新增tokens可报告','缺少行情与历史一致预期，不以生成的数字填充'])
    dump(directory/'PROTOCOL.json',protocol)
    for t,g in guides.items():dump(directory/(t+'_guide.json'),g)
    results={};body_cache={};stopped=None
    for key in protocol['order']:
        case=cases[key];folder=directory/key;folder.mkdir()
        emit(dict(stage='native_start',case=key))
        try:
            found=call_native(case,directory=folder/'native',config_path=ROOT.parent/'apikey.env',
                ledger=ledger,run_id=auth+'_'+key+'_native')
            results[key]=dict(native=found,status='native_returned')
            emit(dict(stage='native_result',case=key,status=found['status'],search_executed=found.get('native_search_executed'),usage=found.get('usage')))
            # A newly unknown request stops the complete experiment before any
            # later role/model. This does not delete the partial result.
            ledger._check(ledger.read())
            if not found.get('native_search_executed') or not found.get('findings'):
                results[key]['status']='no_qualified_research';continue
            sources=[]
            links=list(dict.fromkeys(u for f in found['findings'] for u in f['source_urls']))[:2]
            for url in links:
                if url not in body_cache:
                    emit(dict(stage='original_check',case=key,url=url))
                    body_cache[url]=fetch_article({'url':url,'title':'Native returned source'},datetime.now(timezone.utc).isoformat(),
                        archive_dir=directory/'originals',max_chars=9000)
                body=body_cache[url]
                sources.append({k:body.get(k) for k in ('url','title','published_at','modified_at','first_retrieved_at','version_id','status','eligible','source_verified')})
                sources[-1]['text']=body.get('body_text','')[:2400]
            dump(folder/'source_checks.json',sources)
            # Audit only compact evidence, not provider-internal reasoning.
            compact={k:v for k,v in found.items() if k not in ('raw_model_text',)}
            emit(dict(stage='discussion_start',case=key))
            discussion=discuss_case(case,compact,sources,directory=folder/'discussion',config_path=ROOT.parent/'apikey.env',
                ledger=ledger,run_id=auth+'_'+key+'_discussion')
            results[key].update(status=discussion['status'],discussion=discussion)
            dump(directory/'PARTIAL_RESULTS.json',results)
            ledger._check(ledger.read())
            emit(dict(stage='case_complete',case=key,status=discussion['status']))
        except BudgetStop as exc:
            stopped=dict(type='BudgetStop',reason=str(exc),case=key)
            emit(dict(stage='hard_stop',**stopped));break
    d=ledger.read();new=[r for r in d['requests'] if r.get('authorization_id')==auth]
    known=[r for r in new if type((r.get('usage') or {}).get('total_tokens')) is int]
    result=dict(protocol=protocol,results=results,stopped=stopped,completed_cases=sum(r['status']=='completed' for r in results.values()),
        usage=dict(new_model_requests=len(new),new_native_requests=sum(r['role']=='native_prompt_research' for r in new),
            new_discussion_requests=sum(r['role']!='native_prompt_research' for r in new),
            new_usage_known=len(known)==len(new),new_known_token_subtotal=sum(r['usage']['total_tokens'] for r in known),
            total_cost_known=False,prior_unknown_preserved=True),
        paid_native_search_used=bool(new),scheduler_created=False,trading_actions=False)
    dump(directory/'RESULTS.json',result);emit(dict(stage='finished',completed_cases=result['completed_cases'],usage=result['usage'],stopped=stopped))
    return result

def finish_saved(directory):
    """Reparse preserved completed responses; never submit native search again.

    Used for the provider's empty-annotations compatibility fix. The original
    protocol/responses/RESULTS stay immutable; revised results are a new file.
    """
    from integration.prompt_web_research import parse_research
    directory=Path(directory).resolve()
    original=json.loads((directory/'RESULTS.json').read_text(encoding='utf-8'))
    protocol=original['protocol'];auth=protocol['authorization_id']
    ledger=ExplicitABBudget(protocol['authoritative_ledger'],auth)
    revised={};cache={}
    for key in protocol['order']:
        case=protocol['cases'][key];folder=directory/key
        provider=folder/'native/provider.json'
        if not provider.exists():
            revised[key]=dict(status='native_missing',final=None);continue
        raw=json.loads(provider.read_text(encoding='utf-8'))
        research=parse_research(raw)
        dump(folder/'native/research_compatibility.json',research)
        revised[key]=dict(native=research,status='reparsed')
        if raw.get('status')!='completed' or not research.get('native_search_executed'):
            revised[key]['status']='no_qualified_research';continue
        discussion_dir=folder/'discussion'
        final_file=discussion_dir/'final_review_result.json'
        if final_file.exists():
            revised[key].update(status='completed',discussion=dict(status='completed',final=json.loads(final_file.read_text(encoding='utf-8'))))
            continue
        if (discussion_dir/'critical_discussion_request.json').exists():
            raise BudgetStop('partial discussion exists; never repeat already attempted roles')
        sources=[]
        # Give each finding an original-source check before spending extra
        # checks on multiple copies of the same event. Same rule for both arms.
        leads=research['findings']+[f for f in research.get('rejected_findings',[]) if isinstance(f.get('source_urls'),list) and f['source_urls']]
        links=list(dict.fromkeys([f['source_urls'][0] for f in leads]+
            [u for f in leads for u in f['source_urls']]))[:4]
        for url in links:
            if url not in cache:
                emit(dict(stage='saved_original_check',case=key,url=url))
                cache[url]=fetch_article({'url':url,'title':'Native opened source'},case['analysis_time'],
                    archive_dir=directory/'originals',max_chars=9000)
            body=cache[url]
            source={k:body.get(k) for k in ('url','title','published_at','modified_at','first_retrieved_at','version_id','status','eligible','source_verified','as_of_eligible','historical_eligible')}
            source['text']=body.get('body_text','')[:5000] if body.get('eligible') else ''
            sources.append(source)
        dump(folder/'source_checks.json',sources)
        compact={k:v for k,v in research.items() if k!='raw_model_text'}
        emit(dict(stage='saved_discussion_start',case=key,traceable_findings=len(research['findings'])))
        discussed=discuss_case(case,compact,sources,directory=discussion_dir,config_path=ROOT.parent/'apikey.env',
            ledger=ledger,run_id=auth+'_'+key+'_discussion')
        revised[key].update(status=discussed['status'],discussion=discussed)
        dump(directory/'FINISH_PARTIAL_RESULTS.json',revised)
        emit(dict(stage='saved_case_done',case=key,status=discussed['status']))
    d=ledger.read();rows=[r for r in d['requests'] if r.get('authorization_id')==auth]
    result=dict(protocol=protocol,compatibility_fix='Completed native open_page URLs are traceable leads, not independently verified facts. Identical reparse for all arms; no repeated native requests.',
        results=revised,completed_cases=sum(r['status']=='completed' for r in revised.values()),
        usage=dict(new_model_requests=len(rows),new_native_requests=sum(r['role']=='native_prompt_research' for r in rows),
            new_discussion_requests=sum(r['role']!='native_prompt_research' for r in rows),
            new_known_token_subtotal=sum((r.get('usage') or {}).get('total_tokens',0) for r in rows),
            new_usage_known=all(type((r.get('usage') or {}).get('total_tokens')) is int for r in rows),
            total_cost_known=False,prior_unknown_preserved=True),token_limit_disabled=bool(protocol.get('token_limit_disabled')))
    dump(directory/'FINISHED_RESULTS.json',result)
    emit(dict(stage='saved_finished',completed_cases=result['completed_cases'],usage=result['usage']))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--no-token-limit',action='store_true',help='Use only after explicit user authorization; preserve old ledger limits and history.')
    parser.add_argument('--finish-saved',action='store_true',help='Reparse completed saved provider output, then perform unattempted discussion roles only.')
    args=parser.parse_args()
    if args.finish_saved:finish_saved(args.output)
    else:run(args.output,no_token_limit=args.no_token_limit)
