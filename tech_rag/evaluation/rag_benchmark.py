"""Local-only human-reviewed retrieval/answer benchmark. Drafts are never gold."""
from dataclasses import asdict, replace
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import math
import random
import sqlite3
from contextlib import closing
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ROOT, now
from retrieval_config import RetrievalConfig

TOPICS = {
    'NVDA': ['经营分部与CUDA生态', '晶圆制造伙伴及披露边界', '内存供给与特定产品关联', '封装测试及交付约束',
             '直接与间接客户集中度', '数据中心客户投资与需求', '出口许可适用产品与限制', '产品代际、指引与收入兑现'],
    'MU': ['四业务单元与终端需求', 'HBM与具名下游产品', 'HBM容量代际和量产送样区别', '晶圆工艺与产能约束',
           'CoWoS技术合作与采购区别', 'SOCAMM部署条件与需求', '存储产品性能及测试脚注', '库存、价格、成本与经营指引'],
    'TSM': ['晶圆代工业务与收入范围', '客户集中度与匿名身份', '先进制程收入及统计期间', 'CoWoS变体结构及适用条件',
            '先进封装扩产与有效产能', '技术联盟与实际订单区别', '海外制造与政策约束', '资本开支、良率与收入指引'],
}
FORMS = [
    '关于{topic}，原文支持哪些事实，哪些范围仍未知？',
    '围绕{topic}，新闻Agent应该搜索哪些对象、信号及核实问题？',
    '{topic}发生变化时，影响目标公司的路径及成立条件是什么？',
    '核实{topic}时，哪些数字、期间、产品或脚注不能混用？',
    '现有资料能否据{topic}断言具体供货份额、客户身份或确定收益？请给出证据边界。',
]
VALIDATION_TOPICS={'NVDA':{4},'MU':{4,5},'TSM':{5}}
DEVELOPMENT_TOPICS={'NVDA':{0,1,2,3},'MU':{0,1,2,3},'TSM':{0,2,3,4}}
SHARED_FAMILIES={('MU',4):'TSM_MU_technology_not_procurement',('TSM',5):'TSM_MU_technology_not_procurement',
                 ('NVDA',2):'NVDA_memory_product_scope',('MU',1):'NVDA_memory_product_scope'}

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

def draft_benchmark():
    rows = []
    for ticker, topics in TOPICS.items():
        for index, topic in enumerate(topics):
            # Fact-family groups, never random paraphrase splits.
            split = 'development' if index in DEVELOPMENT_TOPICS[ticker] else ('validation' if index in VALIDATION_TOPICS[ticker] else 'sealed_test')
            for form, template in enumerate(FORMS):
                rows.append(dict(id=f'{ticker}_{index+1:02d}_{form+1}', ticker=ticker, split=split,
                    fact_family=SHARED_FAMILIES.get((ticker,index),f'{ticker}_{index+1:02d}'), question=template.format(topic=topic),
                    review_status='unreviewed', reviewer=None, reviewed_at=None, facts=[], qrels={},
                    prohibited_inferences=[], answerability=None, answer_review=None))
    return dict(schema='human_rag_benchmark_v1', created_at=now(), question_count=120,
                provenance='system drafting; not independent human gold', questions=rows)

def validate_labels(manifest):
    rows = manifest['questions']; families = {}; fact_splits = {}
    for row in rows:
        family = row['fact_family']
        if family in families and families[family] != row['split']:
            raise ValueError('Fact-family leakage across splits')
        families[family] = row['split']
        if row['review_status'] != 'reviewed':
            continue
        if not row.get('reviewer') or not row.get('reviewed_at') or row.get('answerability') not in ('answerable', 'conditional', 'insufficient'):
            raise ValueError('Reviewed labels require reviewer, time and answerability')
        if row['answerability'] != 'insufficient' and not row['facts']:
            raise ValueError('Answerable gold must identify required facts')
        for fact in row['facts']:
            fid = fact['fact_id']
            if fid in fact_splits and fact_splits[fid] != row['split']:
                raise ValueError('Gold fact leakage across splits')
            fact_splits[fid] = row['split']
            if not fact.get('sources'):
                raise ValueError('Gold fact needs original source locations')
        for judgement in row['qrels'].values():
            if type(judgement.get('grade')) is not int or judgement['grade'] not in (0, 1, 2, 3):
                raise ValueError('Evidence relevance requires manual grade 0..3')
            if not set(judgement.get('fact_ids',[]))<={f['fact_id'] for f in row['facts']}:
                raise ValueError('Relevance label references an unknown required fact')
    return rows

def metrics(row, ranking, k=16,answer_review=None):
    if row.get('review_status') != 'reviewed':
        return dict(status='pending_human_review', formal=False)
    selected = list(dict.fromkeys(ranking))[:k]
    labels = row['qrels']; unknown = [cid for cid in selected if cid not in labels]
    if unknown:
        return dict(status='pending_evidence_labels', formal=False, unjudged=len(unknown), returned=len(selected))
    required = {f['fact_id'] for f in row['facts']}
    found = {f for cid in selected for f in labels[cid].get('fact_ids', []) if labels[cid]['grade'] >= 2} & required
    candidate_found = {f for cid in ranking[:100] if cid in labels for f in labels[cid].get('fact_ids', []) if labels[cid]['grade'] >= 2} & required
    grades = [labels[cid]['grade'] for cid in selected]
    dcg = lambda gs: sum((2**g-1)/math.log2(i+2) for i, g in enumerate(gs))
    ideal = dcg(sorted([v['grade'] for v in labels.values()], reverse=True)[:k])
    clusters = [labels[cid].get('duplicate_cluster') for cid in selected if labels[cid]['grade'] >= 2]
    redundancy = None if any(c is None for c in clusters) else (1-len(set(clusters))/len(clusters) if clusters else 0)
    result = dict(status='measured', formal=True, evidence_precision=sum(g >= 2 for g in grades)/len(selected) if selected else 0,
        ndcg=dcg(grades)/ideal if ideal else None,mrr=next((1/(i+1) for i,g in enumerate(grades) if g>=2),0),
        fact_recall=len(found)/len(required) if required else None,
        candidate_fact_recall=len(candidate_found)/len(required) if required else None,
        candidate_labels_complete=all(c in labels for c in ranking[:100]),
        duplicate_fact_ratio=redundancy, recovered_facts=sorted(found), missing_facts=sorted(required-found))
    review = answer_review
    if review and review.get('review_status') == 'reviewed':
        claims = review.get('claims', [])
        result['claim_support_rate'] = sum(c['status'] == 'supported' for c in claims)/len(claims) if claims else None
        citations = review.get('citations', [])
        result['citation_precision'] = sum(c.get('supports_claim') is True for c in citations)/len(citations) if citations else None
        result['omitted_required_facts'] = review.get('omitted_required_facts')
        supported=[c for c in claims if c.get('status')=='supported']
        result['citation_completeness']=sum(c.get('all_support_cited') is True for c in supported)/len(supported) if supported else None
        result['correct_abstention']=review.get('correct_abstention')
        result['false_abstention']=review.get('false_abstention')
        result['severe_errors']=sum(c.get('severe_error') is True for c in claims)
    return result

def comparison_report(records,manifest):
    by_config={};question_map={r['id']:r for r in manifest['questions']}
    for record in records:by_config.setdefault(record['configuration'],{})[record['question_id']]=record
    baseline='baseline' if 'baseline' in by_config else 'current'
    if baseline not in by_config:return dict(status='not_determined',reason='No explicit baseline measurements')
    comparisons={}
    fields=('fact_recall','evidence_precision','ndcg','mrr','duplicate_fact_ratio','claim_support_rate','citation_precision','citation_completeness')
    for name,rows in by_config.items():
        if name==baseline:continue
        shared=sorted(set(rows)&set(by_config[baseline]));summary={}
        for field in fields:
            grouped={}
            for qid in shared:
                a=by_config[baseline][qid]['metrics'];b=rows[qid]['metrics']
                if not a.get('formal') or not b.get('formal') or a.get(field) is None or b.get(field) is None:continue
                grouped.setdefault(question_map[qid]['fact_family'],[]).append((a[field],b[field]))
            pairs=[(sum(a for a,_ in values)/len(values),sum(b for _,b in values)/len(values)) for values in grouped.values()]
            summary[field]=paired_interval([a for a,_ in pairs],[b for _,b in pairs]) if pairs else None
        summary['fact_changes']=[dict(question_id=qid,
            recovered=sorted(set(rows[qid]['metrics'].get('recovered_facts',[]))-set(by_config[baseline][qid]['metrics'].get('recovered_facts',[]))),
            lost=sorted(set(by_config[baseline][qid]['metrics'].get('recovered_facts',[]))-set(rows[qid]['metrics'].get('recovered_facts',[])))) for qid in shared]
        summary['status']='unproven'
        if any(summary.get(k) and summary[k]['upper']<0 for k in ('fact_recall','evidence_precision','claim_support_rate')):summary['status']='regression'
        recall=summary.get('fact_recall');precision=summary.get('evidence_precision');support=summary.get('claim_support_rate')
        if recall and precision and support and recall['lower']>0 and precision['lower']>=0 and support['lower']>=0:
            summary['status']='observed_improvement_on_this_reviewed_set'
        comparisons[name]=summary
    return dict(status='requires_human_interpretation',baseline=baseline,comparisons=comparisons,
                uncertainty_unit='fact-family cluster means; paired bootstrap',winner='not_determined')

def excerpt_overlap(citations):
    groups={};total=0
    for row in citations.values():
        a,b=row['char_start'],row['char_end'];total+=b-a
        groups.setdefault((row['source_id'],row['source_sha256'],row.get('pdf_page'),row.get('paragraph')),[]).append((a,b))
    unique=0
    for spans in groups.values():
        end=-1
        for a,b in sorted(spans):
            unique+=max(0,b-max(a,end));end=max(end,b)
    return dict(overlap_character_ratio=(total-unique)/total if total else 0,total_excerpt_chars=total,unique_source_chars=unique)

def paired_interval(before, after, *, seed=1729, samples=2000):
    if len(before) != len(after) or not before:
        raise ValueError('Paired nonempty samples required')
    differences = [b-a for a, b in zip(before, after)]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(differences) for _ in differences)/len(differences) for _ in range(samples))
    return dict(mean=sum(differences)/len(differences), lower=means[int(.025*samples)], upper=means[int(.975*samples)], n=len(differences))

def configs():
    base = RetrievalConfig()
    rows = [('current', base)]
    for channels in [('keyword',), ('reviewed_dense',), ('graph',), ('keyword','reviewed_dense'), ('keyword','graph'), ('keyword','reviewed_dense','graph')]:
        rows.append(('+'.join(channels)+'_plain', replace(base, channels=channels, diversify=False)))
    rows += [('equal_rrf', replace(base, keyword_weight=1, detail_keyword_weight=1, graph_weight=1, detail_graph_weight=1)),
             ('novelty_only', replace(base, location_penalty=0)), ('location_only', replace(base, novelty=0))]
    return rows

def fusion_grid():
    for kw, graph, k in itertools.product([1,1.5,2,3], [.5,1,1.5,2], [10,30,60,100]):
        yield replace(RetrievalConfig(), keyword_weight=kw, detail_keyword_weight=kw,
                      graph_weight=graph, detail_graph_weight=graph, rrf_k=k, diversify=False)

def diversity_grid(config):
    for novelty,penalty in itertools.product([0,.005,.01,.02,.055],[0,.002,.004]):
        yield replace(config,diversify=True,novelty=novelty,location_penalty=penalty)

def audit_gold_sources(manifest,snapshot):
    from knowledge_versions import file_hash,safe_path
    from common import load,readl,parse_time
    sources={s['source_id']:s for s in load(snapshot.file('source_catalog.json'))}
    for question in manifest['questions']:
        if question['review_status']!='reviewed':continue
        for fact in question['facts']:
            for ref in fact['sources']:
                source=sources[ref['source_id']]
                if ref['source_sha256']!=source['sha256'] or file_hash(safe_path(snapshot.root,source['local_path']))!=source['sha256']:
                    raise ValueError('Gold original version hash mismatch')
                blocks=readl(safe_path(snapshot.root,source['text_path']))
                block=next(b for b in blocks if (b.get('pdf_page'),b.get('paragraph'))==(ref.get('pdf_page'),ref.get('paragraph')))
                text=block['text']
                if type(ref['char_start']) is not int or type(ref['char_end']) is not int or not 0<=ref['char_start']<ref['char_end']<=len(text) or text[ref['char_start']:ref['char_end']]!=ref['quote']:
                    raise ValueError('Gold source coordinate mismatch')
                if parse_time(source['available_at'])>parse_time(manifest['analysis_time']):raise ValueError('Gold uses future evidence')

def build_pool(manifest,output,*,dense=False):
    """Shuffle original excerpts without assigning automatic relevance grades."""
    from research import ResearchEngine
    from knowledge_versions import active_snapshot
    from copy import deepcopy
    value=deepcopy(manifest);snapshot=active_snapshot();engine=ResearchEngine(snapshot=snapshot)
    value.update(as_of='current',analysis_time=now(),knowledge_generation=snapshot.generation)
    clock=dict(analysis_time=value['analysis_time'],historical=False)
    for row in value['questions']:
        candidates={}
        modes=('unified','keyword','graph','dense') if dense else ('unified','keyword','graph')
        for mode in modes:
            engine.build(row['ticker'],row['question'],'current',{'use_dense':dense,'context_radius':0},mode=mode,temporal_context=clock)
            for ranked in engine.last_ranking_audit['candidates'][:30]:
                cid=ranked['chunk_id'];chunk=engine.chunks_by_id[cid]
                if not engine.verify_chunk(chunk)[0]:continue
                candidates[cid]={k:chunk.get(k) for k in ('chunk_id','source_id','source_sha256','source_url','pdf_page','paragraph','char_start','char_end','text','available_at')}
        shuffled=list(candidates.values());random.Random(row['id']).shuffle(shuffled)
        row['candidate_evidence']=shuffled
        row['qrels']={c['chunk_id']:dict(grade=None,fact_ids=[],duplicate_cluster=None) for c in shuffled}
        row['pool_note']='System candidate pool; inspect raw sources for shared omissions. This is not gold.'
    value['draft_content_sha256']=digest({k:v for k,v in value.items() if k!='draft_content_sha256'})
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    return dict(status='pending_human_review',question_count=len(value['questions']),knowledge_generation=snapshot.generation)

def run(manifest, output, *, split='development', dense=True, grid=False, sealed=False):
    """No generation/model API; BGE is optional local inference only."""
    from research import ResearchEngine
    from knowledge_versions import active_snapshot
    rows = [r for r in validate_labels(manifest) if r['split'] == split]
    if split == 'sealed_test' and (grid or not sealed):
        raise ValueError('Sealed evaluation requires explicit single frozen configuration')
    if not rows or any(r['review_status'] != 'reviewed' for r in rows):
        return dict(status='pending_human_review', evaluated=0, winner='not_determined')
    snapshot=active_snapshot(generation=manifest.get('knowledge_generation'))
    audit_gold_sources(manifest,snapshot)
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    if split == 'sealed_test':
        registry=ROOT/'state/rag_benchmark_registry.sqlite3'
        with closing(sqlite3.connect(registry)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS sealed_runs(dataset_id TEXT PRIMARY KEY,output TEXT NOT NULL)')
            identity=digest([(r['id'],r['question'],r['split'],r['fact_family']) for r in manifest['questions']])
            db.execute('INSERT INTO sealed_runs VALUES (?,?)',(identity,str(output.resolve())))
        with (output/'SEALED_STARTED.json').open('x', encoding='utf-8') as f:
            json.dump({'manifest_sha256':digest(manifest)}, f)
        selected = [('baseline', RetrievalConfig.from_value(manifest['baseline_config'])),('frozen', RetrievalConfig.from_value(manifest['frozen_config']))]
    else:
        selected = [(c.version,c) for c in fusion_grid()] if grid else configs()
    records = []
    def measure(name,config):
        engine = ResearchEngine(retrieval_config=config,snapshot=snapshot)
        for row in rows:
            at = row.get('as_of') or manifest.get('as_of')
            if not at:
                raise ValueError('Freeze knowledge cutoff before evaluation')
            clock=dict(analysis_time=manifest['analysis_time'],historical=False) if at=='current' else None
            packet = engine.build(row['ticker'], row['question'], at, {'use_dense':dense},temporal_context=clock)
            candidates = engine.last_ranking_audit['candidates']
            records.append(dict(question_id=row['id'], configuration=name, config=asdict(config),
                config_version=config.version, packet_fingerprint=packet['fingerprints'],
                ranking=[x['chunk_id'] for x in candidates], selected=[x['chunk_id'] for x in packet['results']],
                metrics=metrics(row,[x['chunk_id'] for x in packet['results']],answer_review=row.get('answer_reviews',{}).get(config.version)),
                excerpt_overlap=excerpt_overlap(packet['citations']),candidate_metrics=metrics(row,[x['chunk_id'] for x in candidates],100)))
    for name,config in selected:measure(name,config)
    if grid:
        def score(config):
            measured=[r['metrics'] for r in records if r['config_version']==config.version and r['metrics'].get('formal') and r['metrics'].get('ndcg') is not None]
            return sum(m['ndcg'] for m in measured)/len(measured) if len(measured)==len(rows) else -1
        leaders=sorted((c for _,c in selected),key=lambda c:(-score(c),c.version))[:3]
        for leader in leaders:
            for config in diversity_grid(leader):measure(config.version,config)
    result = dict(status='measured_requires_review', winner='not_determined', manifest_sha256=digest(manifest), records=records,
                  comparison=comparison_report(records,manifest),generation_effectiveness='unknown_without_answer_reviews')
    (output/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--draft',action='store_true');p.add_argument('--input',type=Path)
    p.add_argument('--pool',action='store_true')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--split',default='development',choices=['development','validation','sealed_test'])
    p.add_argument('--no-dense',action='store_true');p.add_argument('--grid',action='store_true');p.add_argument('--unseal-once',action='store_true')
    a=p.parse_args()
    if a.draft:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:json.dump(draft_benchmark(),f,ensure_ascii=False,indent=2)
        print('120 draft questions saved locally; human review pending')
    elif a.pool:
        print(json.dumps(build_pool(json.loads(a.input.read_text(encoding='utf-8')),a.output,dense=not a.no_dense),ensure_ascii=False))
    else:
        result=run(json.loads(a.input.read_text(encoding='utf-8')),a.output,split=a.split,dense=not a.no_dense,grid=a.grid,sealed=a.unseal_once)
        print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
