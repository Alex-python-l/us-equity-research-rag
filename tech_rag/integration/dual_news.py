"""RAG-first dual news: company Yahoo + provider-native industry discovery.

Models have no project search tool, DB writer, or trading permission. Original
body verification and analyst/reviewer contracts reuse the existing pipeline.
"""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import uuid
from common import ROOT,load,dump,parse_time
from integration.industry_pipeline import prepare_research,_snapshot_fingerprints,run_industry_research,_validate_prepared,_limits


def merge_route_candidates(company,industry):
    output=[];by_url={}
    for route,rows in (('company_yahoo',company),('industry_native',industry)):
        for row in rows[:3]:
            url=row.get('url')
            if not url:continue
            if url in by_url:
                by_url[url]['discovery_routes'].append(route);continue
            item=deepcopy(row);item['discovery_routes']=[route];by_url[url]=item
    queues=[[by_url[row['url']] for row in rows[:3] if row.get('url') in by_url] for rows in (company,industry)]
    for index in range(max((len(q) for q in queues),default=0)):
        for queue in queues:
            if index<len(queue) and queue[index] not in output:output.append(queue[index])
    return output


def prepare_dual_research(ticker,question,as_of='current',*,directory,config_path=None,
    backend='local',use_dense=True,temporal_context=None,budget=None,budget_ledger=None,run_id=None,
    base_prepared=None,company_snapshot=None,native_snapshot=None,article_provider=None,
    company_search=None,native_discover=None,industry_baseline=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    limits=_limits(budget if budget is not None else (base_prepared or {}).get('budget'))
    if limits['max_queries']<2:
        raise ValueError('Dual news requires two route slots; refused before network or paid requests')
    base=deepcopy(base_prepared) if base_prepared is not None else prepare_research(
        ticker,question,as_of,arm='B',backend=backend,use_dense=use_dense,fetch_news=False,
        archive_dir=directory/'background',temporal_context=temporal_context,budget=budget)
    if base_prepared is not None:
        from aliases import resolve_company
        _validate_prepared(base,resolve_company(ticker)['research_symbol'],question,'B',as_of,temporal_context,budget)
    at=base['analysis_time']
    if base['historical']:
        base.update(news_mode='native',native_discovery=dict(status='unsupported_historical',items=[],
            native_search_executed=False,model_requests=0))
        base['phase_trace'].append(dict(phase='dual_live_news_disabled_for_historical'))
        base['fingerprints']=_snapshot_fingerprints(base);dump(directory/'prepared.json',base);return base
    if company_snapshot is None:
        from integration.live_news import search_live_news
        company_snapshot=(company_search or search_live_news)(ticker,at,False,
            lookback_days=limits['lookback_days'],limit=limits['results_per_query'])
    if company_snapshot.get('items') and (company_snapshot.get('ticker')!=base['ticker']
        or parse_time(company_snapshot.get('analysis_time'))!=parse_time(at)):
        raise ValueError('Company Yahoo snapshot company/time mismatch')
    dump(directory/'company_yahoo_snapshot.json',company_snapshot)
    if industry_baseline is not None:
        native_snapshot=dict(status='explicit_Yahoo_industry_baseline',items=industry_baseline,
            native_search_executed=False,model_requests=0)
    elif native_snapshot is None:
        from integration.native_search import discover_native
        native_snapshot=(native_discover or discover_native)(base,directory=directory/'native_discovery',
            config_path=config_path,budget_ledger=budget_ledger,run_id=(run_id or 'N6_'+uuid.uuid4().hex)+'_native')
    if native_snapshot.get('native_search_executed'):
        from integration.native_search import sha
        if (native_snapshot.get('ticker')!=base['ticker'] or
            parse_time(native_snapshot.get('analysis_time'))!=parse_time(at) or
            native_snapshot.get('background_sha256')!=sha(base.get('industry_context'))):
            raise ValueError('Native discovery snapshot company/time/background mismatch')
    elif industry_baseline is None and native_snapshot.get('items'):
        native_snapshot=deepcopy(native_snapshot)
        native_snapshot['discarded_unexecuted_items']=len(native_snapshot['items'])
        native_snapshot['items']=[]
    merged=merge_route_candidates(company_snapshot.get('items',[]),native_snapshot.get('items',[]))
    company=[c for c in merged if 'company_yahoo' in c['discovery_routes']]
    industry=[c for c in merged if 'industry_native' in c['discovery_routes']]
    start=(parse_time(at)-timedelta(days=limits['lookback_days'])).isoformat()
    tasks=[]
    for route in ('company_yahoo','industry_native'):
        tasks.append(dict(task_id='DUAL_'+route,query_hash='DUAL_'+route,
            query_en=ticker if route=='company_yahoo' else question[:500],
            query_zh=question,time_window=dict(start=start,end=at),
            background_evidence_ids=[],route=route,
            query_semantics='Already executed route snapshot; no new search by this routing task.'))
    def router(task,as_of,**kwargs):
        rows=company if task['route']=='company_yahoo' else industry
        return dict(items=rows,calls=0,real_network_executed=False,reused_snapshot=True,
            route=task['route'],original_route_status=company_snapshot.get('status') if task['route']=='company_yahoo' else native_snapshot.get('status'))
    p=prepare_research(ticker,question,'current',arm='B',backend=backend,use_dense=use_dense,
        fetch_news=True,archive_dir=directory/'verified',temporal_context=dict(analysis_time=at,historical=False),
        budget=limits,frozen_industry_context=base['industry_context'],frozen_search_tasks=tasks,
        search_provider=router,article_provider=article_provider)
    for article in p['articles']:
        original=next((c for c in merged if c['url']==article.get('url')),None)
        article['discovery_routes']=original['discovery_routes'] if original else []
    p.update(news_mode='native' if industry_baseline is None else 'yahoo_baseline',
        company_yahoo=company_snapshot,native_discovery=native_snapshot,
        frontloaded_background_trace=base['phase_trace'],candidate_route_map={c['url']:c['discovery_routes'] for c in merged})
    p['phase_trace']=[*base['phase_trace'],dict(phase='company_direct_yahoo',status=company_snapshot.get('status')),
        dict(phase='industry_provider_native_search' if industry_baseline is None else 'industry_Yahoo_baseline',
            status=native_snapshot.get('status'),native_search_executed=native_snapshot.get('native_search_executed',False)),
        *[t for t in p['phase_trace'] if t['phase']!='frontloaded_company_rag_reused_snapshot']]
    p['fingerprints']=_snapshot_fingerprints(p);dump(directory/'prepared.json',p)
    return p


def run_dual_research(ticker,question,as_of='current',*,backend='local',model_mode='controlled',
    llm_config_path=None,use_dense=True,archive_dir=None,budget_ledger=None,run_id=None,prepared=None,
    temporal_context=None,budget=None,workflow='brief',company_snapshot=None,native_snapshot=None,
    article_provider=None,base_prepared=None,**kwargs):
    run_id=run_id or 'DUAL6_'+uuid.uuid4().hex
    directory=Path(archive_dir or ROOT/'output/industry_runtime'/run_id)
    if (directory/'result.json').exists():raise ValueError('Run exists; duplicate submission refused')
    if any(value is not None for value in kwargs.values()):
        raise ValueError('Unsupported native-route argument; external search providers are not accepted')
    from integration.effect_cost import BatchBudget
    if model_mode=='online' and budget_ledger is None:
        budget_ledger=BatchBudget(directory/'budget.json',max_runs=2,per_run=3,max_requests=4,max_tokens=120000)
    if model_mode=='online':
        if hasattr(budget_ledger,'read'):BatchBudget._check(budget_ledger.read())
    if prepared is None:
        if model_mode!='online' and native_snapshot is None:
            # Controlled mode never initializes credentials or pretends to browse.
            native_snapshot=dict(status='native_online_not_requested',items=[],model_requests=0,native_search_executed=False)
            company_snapshot=company_snapshot or dict(status='company_live_not_requested',items=[])
        prepared=prepare_dual_research(ticker,question,as_of,directory=directory/'preparation',
            config_path=llm_config_path,backend=backend,use_dense=use_dense,temporal_context=temporal_context,
            budget=budget,budget_ledger=budget_ledger,run_id=run_id,base_prepared=base_prepared,
            company_snapshot=company_snapshot,native_snapshot=native_snapshot,article_provider=article_provider)
    result=run_industry_research(ticker,question,as_of,backend=backend,model_mode=model_mode,
        llm_config_path=llm_config_path,use_dense=use_dense,archive_dir=directory,prepared=prepared,
        arm='B',run_id=run_id,temporal_context=temporal_context,budget=budget,budget_ledger=budget_ledger,workflow=workflow)
    discovery=prepared.get('native_discovery',{})
    result['news_mode']=prepared.get('news_mode');result['native_search_executed']=discovery.get('native_search_executed',False)
    result['native_discovery_usage']=dict(model_requests=discovery.get('model_requests',0),
        provider_tokens=(discovery.get('usage') or {}).get('total_tokens'),
        usage_known=discovery.get('model_requests',0)==0 or type((discovery.get('usage') or {}).get('total_tokens')) is int,
        visible_server_search_calls=discovery.get('visible_server_search_calls'),
        seconds=discovery.get('seconds'),scope='Additional discovery cost, not included in analysis-only run_usage.')
    result['total_model_requests']=result['run_usage']['model_requests']+discovery.get('model_requests',0)
    known=result['run_usage']['usage_known'] and result['native_discovery_usage']['usage_known']
    result['combined_provider_usage']=dict(model_requests=result['total_model_requests'],usage_known=known,
        total_tokens=result['run_usage']['tokens']['total_tokens']+(discovery.get('usage') or {}).get('total_tokens',0) if known else None,
        prices_verified=False,currency_cost=None)
    dump(directory/'result.json',result);return result
