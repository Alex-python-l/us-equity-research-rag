"""One explicitly requested, bounded NVDA/MU search-scope experiment.

No scheduler, trades, native paid search, automatic retries, or budget resets.
Reports discovery and source verification separately. All experiment files are
isolated from production monitoring plans and checkpoint databases.
"""
import argparse,json,hashlib,sys,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import ROOT,dump
from monitor_planner import MonitorPlanner
from integration.industry_news import search_yahoo
from integration.news_body import fetch_article

def utcnow():return datetime.now(timezone.utc).isoformat()
def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)
def sid(value):return hashlib.sha256(value.encode()).hexdigest()[:20]

def plan(output):
    planner=MonitorPlanner();out={}
    for ticker,name in [('NVDA','NVIDIA'),('MU','Micron')]:
        started=time.perf_counter();guide=planner.guide(ticker)
        selected=[next(t for t in guide['topics'] if t['category']==c) for c in ('upstream','downstream')]
        dump(output/(ticker+'_guide.json'),guide)
        dump(output/(ticker+'_audit.json'),planner.get_guide(guide['guide_id']))
        out[ticker]=dict(guide_id=guide['guide_id'],guide_chars=len(guide['agent_prompt']),guide_model_calls=0,
            guide_seconds=round(time.perf_counter()-started,3),
            arms={'A':dict(label='公司代码/公司名基线',queries=[ticker,name,name+' stock']),
                  'B':dict(label='公司代码+RAG上下游',queries=[ticker]+[t['search_query'] for t in selected])})
    return out

def gather(output):
    output.mkdir(parents=True,exist_ok=True)
    lock=output/'STARTED.json'
    if lock.exists():raise RuntimeError('Experiment already started; no automatic rerun.')
    frozen=datetime.now(timezone.utc);start=frozen-timedelta(days=7)
    with lock.open('x',encoding='utf-8') as f:json.dump({'started_at':frozen.isoformat()},f)
    import yfinance as yf
    yf.set_tz_cache_location(str(output/'yahoo_sdk_cache'))
    specification=dict(version='single_round_monitor_scope_ab_v1',as_of=frozen.isoformat(),lookback_start=start.isoformat(),
        tickers=['NVDA','MU'],queries_per_arm=3,results_per_query=4,max_bodies_per_arm=4,
        provider='same installed Yahoo search adapter',transport_cap_per_query=10,
        body_transport_cap=4,model_judge_cap=2,model_ledger=str(ROOT/'state/rag_chat/budget.json'),
        baseline='ticker, company name, company name + stock',treatment='same ticker search, actual RAG upstream and downstream search_query',
        shared_query_policy='identical query shares one frozen result, charged as logical call to each arm',
        body_selection='provider order interleaved across queries, same four-body limit per arm, shared URL acquisition',
        findings_limitations=['n=1 per company, no statistical significance','relevance ranking is not exhaustive',
            'no historical consensus or market data','metadata discovery is not a verified fact','MU size category not asserted'])
    companies=plan(output);specification['companies']=companies;dump(output/'PROTOCOL.json',specification)
    shared={};bodies={};logical=0;actual=0
    for ticker in ('NVDA','MU'):
        arms=companies[ticker]['arms']
        # Freeze common ticker sample once, then alternate which condition gets
        # its two additional queries first to limit systematic ordering bias.
        order=[('A',0),('B',0)]+([(a,i) for a in ('B','A') for i in (1,2)] if ticker=='NVDA' else [(a,i) for a in ('A','B') for i in (1,2)])
        for arm,index in order:
            query=arms[arm]['queries'][index];logical+=1;key=sid(query)
            if key not in shared:
                actual+=1;emit(dict(stage='search',ticker=ticker,arm=arm,query=query))
                raw=search_yahoo({'task_id':ticker+'_'+arm+'_'+str(index),'query_en':query,
                    'time_window':{'start':start.isoformat(),'end':frozen.isoformat()}},frozen.isoformat(),
                    max_results=4,transport_limit=10,cache_dir=output/'discovery_raw')
                shared[key]=raw;dump(output/('search_'+key+'.json'),raw)
            raw=shared[key]
            result=dict(query=query,shared_query_key=key,provider_status=raw['status'],seconds=raw['seconds'],
                network_calls=raw.get('network_calls'),items=raw.get('items',[]),errors=raw.get('errors',[]))
            arms[arm].setdefault('searches',[]).append((index,result))
            emit(dict(stage='search_result',ticker=ticker,arm=arm,status=raw['status'],items=len(raw.get('items',[])),network_calls=raw.get('network_calls')))
        for arm in ('A','B'):
            searches=[r for _,r in sorted(arms[arm]['searches'])];arms[arm]['searches']=searches
            unique={};ordered=[]
            for rank in range(4):
                for search in searches:
                    if rank>=len(search['items']):continue
                    item=search['items'][rank];url=item['url']
                    if url not in unique:
                        entry={**item,'queries':[search['query']],'provider_rank':rank+1}
                        unique[url]=entry;ordered.append(entry)
                    elif search['query'] not in unique[url]['queries']:unique[url]['queries'].append(search['query'])
            arms[arm]['candidates']=ordered
            for item in ordered[:4]:
                url=item['url'];key=sid(url)
                if key not in bodies:
                    emit(dict(stage='original',ticker=ticker,arm=arm,url=url))
                    # Judged now, not at the earlier discovery freeze. We store
                    # both times and do not claim historical point-in-time eligibility.
                    body=fetch_article(item,utcnow(),archive_dir=output/'originals',max_chars=9000)
                    bodies[key]=body;dump(output/('body_'+key+'.json'),body)
                body=bodies[key]
                item['body_id']=key;item['body_status']=body['status'];item['body_eligible']=body.get('eligible',False)
                item['original_published_at']=body.get('published_at');item['original_version']=body.get('version_id')
            statuses=Counter(s['provider_status'] for s in searches)
            arms[arm]['metrics']=dict(logical_queries=3,search_statuses=dict(statuses),unique_candidates=len(ordered),
                returned_items=sum(len(s['items']) for s in searches),body_attempts=min(4,len(ordered)),
                eligible_originals=sum(bool(i.get('body_eligible')) for i in ordered),
                search_seconds_sum=round(sum(s['seconds'] for s in searches),3),
                verified_facts=None,new_information=None,expectation_gap='unknown',market_reaction='unknown')
            emit(dict(stage='arm_complete',ticker=ticker,arm=arm,metrics=arms[arm]['metrics']))
        a={x['url'] for x in arms['A']['candidates']};b={x['url'] for x in arms['B']['candidates']}
        companies[ticker]['overlap']=dict(shared_urls=len(a&b),a_only=len(a-b),b_only=len(b-a))
        dump(output/(ticker+'_results.json'),companies[ticker])
    result=dict(protocol=specification,companies=companies,finished_at=utcnow(),logical_queries=logical,
        actual_sdk_queries=actual,real_news_http_attempts=sum(r.get('network_calls',0) for r in shared.values()),
        unique_body_attempts=len(bodies),body_network_executed=sum(bool(b.get('real_network_executed')) for b in bodies.values()),
        model_calls=0,scheduler_created=False,trading_actions=False)
    dump(output/'RESULTS.json',result);emit(dict(stage='collection_complete',output=str(output),sdk_queries=actual,
        real_news_http_attempts=result['real_news_http_attempts'],unique_body_attempts=len(bodies)))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args();gather(Path(args.output).resolve())
