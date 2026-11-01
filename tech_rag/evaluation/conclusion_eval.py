"""Conclusion-level paired audit and strict archived replay. No model calls.

Programmatic warnings are screening signals, not semantic proof or a fabricated
effectiveness score. A winner needs blinded expert review or a preregistered
testable prediction; source/candidate counts are never the objective.
"""
from copy import deepcopy
from pathlib import Path
from datetime import datetime
import argparse,json,math,re,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import parse_time,dump

def inspect_final(final,sources=None,prior_expectations=None):
    issues=[];gap=str(final.get('expectation_gap',''))
    if not prior_expectations and re.search(r'可确认.{0,12}预期差|已有预期|预期差有限|净增量有限|已被充分定价',gap):
        issues.append('unsupported_expectation_gap')
    if not final.get('final_conclusion'):issues.append('missing_final_conclusion')
    if sources is not None and not any(s.get('eligible') for s in sources):issues.append('no_eligible_original_body')
    if final.get('research_reliability')=='needs_review':issues.append('model_conclusion_requires_review')
    return dict(final_conclusion=final.get('final_conclusion'),impact_paths=final.get('impact_paths',[]),
        expectation_gap=final.get('expectation_gap','unknown'),market_reaction=final.get('market_reaction','unknown'),
        rejected_claims=final.get('rejected_claims',[]),remaining_unknowns=final.get('remaining_unknowns',[]),
        next_actions=final.get('next_actions',[]),issues=issues,original_final=deepcopy(final))

def compare_pair(ticker,analysis_time,a,b,*,sources_a=None,sources_b=None,prior_expectations=None):
    # Truthy arbitrary metadata must not suppress a missing-baseline warning.
    valid_prior=[]
    for row in prior_expectations or []:
        try:
            if (row.get('version_id') and parse_time(row['published_at'])<parse_time(row['first_disclosed_at'])
                and parse_time(row['published_at'])<=parse_time(row['acquired_at'])<=parse_time(analysis_time)):
                valid_prior.append(row)
        except (ValueError,KeyError,TypeError,AttributeError):pass
    return dict(ticker=ticker,analysis_time=analysis_time,status='paired_review_required' if a and b else 'incomplete_pair',
        A=inspect_final(a,sources_a,valid_prior) if a else None,B=inspect_final(b,sources_b,valid_prior) if b else None,
        winner='not_determined',evaluation_target='final_conclusions_not_news_candidate_counts',
        review_dimensions=['结论与证据是否一致','影响路径是否具体且有条件','事前预期是否可回查','反证和分歧是否保留','未知项是否被误填为事实'],
        automated_audit_is_semantic_proof=False)

def outcome_metrics(outcome):
    unknown=dict(status='unknown',raw_return_pct=None,benchmark_return_pct=None,relative_return_pct=None,causal_attribution='unknown')
    try:
        start=datetime.fromisoformat(outcome['entry_date']);end=datetime.fromisoformat(outcome['exit_date'])
        if start>=end:return unknown
        prices=outcome['stock'];bench=outcome['benchmark']
        if len(prices)!=2 or len(bench)!=2 or not all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in [*prices,*bench]):return unknown
        raw=(prices[1]/prices[0]-1)*100;base=(bench[1]/bench[0]-1)*100
        return dict(status='computed_from_supplied_endpoints',entry_date=outcome['entry_date'],exit_date=outcome['exit_date'],
            raw_return_pct=raw,benchmark_return_pct=base,relative_return_pct=raw-base,causal_attribution='unknown')
    except (ValueError,TypeError,KeyError):return unknown

def replay_cell(cell):
    at=parse_time(cell['analysis_time'])
    if cell.get('memory_A',[])!=cell.get('memory_B',[]):return dict(status='rejected_memory_mismatch',ticker=cell['ticker'])
    # All evidence supplied to the historical judgment must already have been
    # both public AND acquired. Later outcome rows are separate evaluation data.
    evidence=cell.get('evidence')
    if not isinstance(evidence,list) or not evidence:return dict(status='rejected_missing_vintage_evidence',ticker=cell['ticker'])
    try:
        for row in evidence:
            public=parse_time(row['published_at']);acquired=parse_time(row['acquired_at'])
            if not row.get('version_id') or public>acquired or acquired>at:raise ValueError('future/unversioned evidence')
        for row in cell.get('memory_A',[]):
            if any(parse_time(row[k])>at for k in ('checked_at','recorded_at')):raise ValueError('future memory')
    except (ValueError,KeyError,TypeError):return dict(status='rejected_temporal_leakage',ticker=cell['ticker'])
    priors=[]
    try:
        for row in cell.get('prior_expectations',[]):
            public=parse_time(row['published_at']);acquired=parse_time(row['acquired_at'])
            first=row.get('first_disclosed_at') or cell['first_disclosed_at']
            if not row.get('version_id') or public>acquired or acquired>at or public>=parse_time(first):raise ValueError('invalid prior vintage')
            priors.append({**row,'first_disclosed_at':first})
    except (ValueError,KeyError,TypeError):return dict(status='rejected_prior_expectations_vintage',ticker=cell['ticker'])
    comparison=compare_pair(cell['ticker'],cell['analysis_time'],cell.get('A'),cell.get('B'),prior_expectations=priors)
    # This is audit/replay of pre-existing judgments, not a newly generated LLM
    # backtest. Directional win rates are undefined for conditional research.
    return dict(status='archived_conclusions_replayed',comparison=comparison,outcome=outcome_metrics(cell.get('outcome',{})),
        pnl=None,win_rate=None,model_calls=0,execution_allowed=False)

def audit_saved(directory):
    folder=Path(directory);path=folder/'FINISHED_RESULTS.json'
    data=json.loads(path.read_text(encoding='utf-8'));protocol=data['protocol'];pairs=[]
    for ticker in dict.fromkeys(c['ticker'] for c in protocol['cases'].values()):
        finals={};sources={}
        for arm in ('A','B'):
            key=ticker+'_'+arm;row=data['results'].get(key,{})
            finals[arm]=row.get('discussion',{}).get('final') if row.get('status')=='completed' else None
            source=folder/key/'source_checks.json';sources[arm]=json.loads(source.read_text(encoding='utf-8')) if source.exists() else []
        pairs.append(compare_pair(ticker,protocol['judged_at'],finals['A'],finals['B'],sources_a=sources['A'],sources_b=sources['B']))
    return dict(schema_version='conclusion_paired_audit_v1',source=str(path.resolve()),pairs=pairs,
        complete_pairs=sum(p['status']=='paired_review_required' for p in pairs),winner='not_determined',
        historical_backtest_eligible=False,reason='Original sources were acquired after frozen judgment; no historical-vintage proof',
        model_calls=0,usage=data.get('usage'))

def write_report(result,path):
    lines=['结论级 A/B 审查：比较终审结论，不比较新闻候选数量。','自动检查不是语义证明；未取得盲评或预注册预测检验，不自动宣布胜出。','']
    pairs=list(result.get('pairs',[]))
    for cell in result.get('cells',[]):
        if cell.get('comparison'):pairs.append(cell['comparison'])
        else:lines.append('历史样本：'+str(cell.get('ticker',''))+' | '+cell['status'])
    for p in pairs:
        lines.extend([p['ticker']+' | '+p['status']])
        for arm in ('A','B'):
            row=p.get(arm)
            lines.append(arm+'：'+(row['final_conclusion'] if row else '未完成'))
            if row:lines.extend(['审查提示：'+(', '.join(row['issues']) or '程序未检出，不代表结论正确'),'预期差：'+str(row['expectation_gap'])])
        lines.append('')
    Path(path).write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--saved',type=Path);p.add_argument('--replay',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if bool(args.saved)==bool(args.replay):p.error('Choose exactly one of --saved or --replay')
    if args.saved:result=audit_saved(args.saved)
    else:
        manifest=json.loads(args.replay.read_text(encoding='utf-8'));cells=[replay_cell(c) for c in manifest['cells']]
        result=dict(cells=cells,eligible_cells=sum(c['status']=='archived_conclusions_replayed' for c in cells),model_calls=0,winner='not_determined')
    args.output.parent.mkdir(parents=True,exist_ok=True);dump(args.output,result);write_report(result,args.output.with_suffix('.txt'))
    print(json.dumps(dict(output=str(args.output.resolve()),model_calls=0,winner='not_determined'),ensure_ascii=False))
