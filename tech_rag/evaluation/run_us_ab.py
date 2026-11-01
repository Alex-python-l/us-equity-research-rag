"""Paired full-graph research: shared clock and pre-run memory, A=no RAG.

Runs a finite experiment only. Does not enable a scheduler, restart legacy
experiments, or clear a ledger gate. No automatic winner or trading orders.
"""
from datetime import datetime,timezone
from copy import deepcopy
from pathlib import Path
import argparse,json,sys,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import dump
from integration.research_memory import ResearchMemory
from evaluation.conclusion_eval import compare_pair,write_report

def run_pairs(tickers,directory,*,paid_confirmed=False,runner=None,memory=None):
    if paid_confirmed is not True:raise ValueError('Explicit paid one-shot A/B confirmation required')
    if not tickers or len(tickers)!=len(set(tickers)):raise ValueError('Nonempty unique tickers required')
    if runner is None:
        from integration.us_research import run_full_research,preflight_models
        from integration.effect_cost import BudgetStop
        preflight=preflight_models(cases=2*len(tickers))
        folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
        dump(folder/'PREFLIGHT.json',preflight)
        if not preflight['ready']:raise BudgetStop('Original ledger insufficient for full paired experiment; no model/tool requests sent')
        runner=run_full_research
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    at=datetime.now(timezone.utc).isoformat();memory=memory or ResearchMemory()
    namespace='AB_'+uuid.uuid4().hex
    # All snapshots captured before any A/B output or review is recorded.
    snapshots={t:memory.context(t,at,namespace='production',arm='B') for t in tickers}
    protocol=dict(schema_version='us_full_graph_conclusion_ab_v1',analysis_time=at,tickers=tickers,
        shared_memory_snapshots=snapshots,namespace=namespace,comparison='A:no RAG; B:company-specific RAG if covered',
        evaluation_target='final_conclusions',time_window_semantics='live collection; not historical point-in-time backtest')
    with (folder/'PROTOCOL.json').open('x',encoding='utf-8') as f:json.dump(protocol,f,ensure_ascii=False,indent=2)
    pairs=[];stopped=None
    for index,ticker in enumerate(tickers):
        arms={}
        for arm in (('A','B') if index%2==0 else ('B','A')):
            try:
                arms[arm]=runner(ticker,directory=folder/(ticker+'_'+arm),paid_confirmed=True,as_of=at,arm=arm,
                    memory_snapshot=deepcopy(snapshots[ticker]),memory_namespace=namespace)
                dump(folder/'PARTIAL.json',dict(pairs=pairs,current=dict(ticker=ticker,arms=arms)))
            except Exception as exc:
                stopped=dict(ticker=ticker,arm=arm,error_type=type(exc).__name__);break
        final=lambda arm:arms.get(arm,{}).get('final')
        pairs.append(compare_pair(ticker,at,final('A'),final('B')))
        if stopped:break
    result=dict(protocol=protocol,pairs=pairs,stopped=stopped,
        complete_pairs=sum(p['status']=='paired_review_required' for p in pairs),winner='not_determined',execution_allowed=False)
    dump(folder/'RESULTS.json',result);write_report(result,folder/'REPORT.txt');return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--tickers',nargs='+',default=['NVDA','MU']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--confirm-paid',action='store_true');p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    if args.prepare_only:
        from integration.us_research import preflight_models
        report=preflight_models(cases=2*len(args.tickers));args.output.mkdir(parents=True,exist_ok=True)
        dump(args.output/'PREFLIGHT.json',report);print(json.dumps(report,ensure_ascii=False));sys.exit(0)
    result=run_pairs(args.tickers,args.output,paid_confirmed=args.confirm_paid)
    print(json.dumps(dict(complete_pairs=result['complete_pairs'],stopped=result['stopped'],winner='not_determined'),ensure_ascii=False))
