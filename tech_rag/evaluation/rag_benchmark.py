"""Local-only human-reviewed retrieval/answer benchmark. Drafts are never gold."""
from dataclasses import asdict, replace
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import math
import random
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

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

def draft_benchmark():
    rows = []
    for ticker, topics in TOPICS.items():
        for index, topic in enumerate(topics):
            # Fact-family groups, never random paraphrase splits.
            split = 'development' if index < 4 else ('validation' if index < (6 if ticker == 'MU' else 5) else 'sealed_test')
            for form, template in enumerate(FORMS):
                rows.append(dict(id=f'{ticker}_{index+1:02d}_{form+1}', ticker=ticker, split=split,
                    fact_family=f'{ticker}_{index+1:02d}', question=template.format(topic=topic),
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
    return rows

def metrics(row, ranking, k=16):
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
        ndcg=dcg(grades)/ideal if ideal else None, fact_recall=len(found)/len(required) if required else None,
        candidate_fact_recall=len(candidate_found)/len(required) if required else None,
        candidate_labels_complete=all(c in labels for c in ranking[:100]),
        duplicate_fact_ratio=redundancy, recovered_facts=sorted(found), missing_facts=sorted(required-found))
    review = row.get('answer_review')
    if review and review.get('review_status') == 'reviewed':
        claims = review.get('claims', [])
        result['claim_support_rate'] = sum(c['status'] == 'supported' for c in claims)/len(claims) if claims else None
        citations = review.get('citations', [])
        result['citation_precision'] = sum(c.get('supports_claim') is True for c in citations)/len(citations) if citations else None
        result['omitted_required_facts'] = review.get('omitted_required_facts')
    return result

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

def run(manifest, output, *, split='development', dense=True, grid=False, sealed=False):
    """No generation/model API; BGE is optional local inference only."""
    from research import ResearchEngine
    rows = [r for r in validate_labels(manifest) if r['split'] == split]
    if split == 'sealed_test' and (grid or not sealed):
        raise ValueError('Sealed evaluation requires explicit single frozen configuration')
    if not rows or any(r['review_status'] != 'reviewed' for r in rows):
        return dict(status='pending_human_review', evaluated=0, winner='not_determined')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    if split == 'sealed_test':
        with (output/'SEALED_STARTED.json').open('x', encoding='utf-8') as f:
            json.dump({'manifest_sha256':digest(manifest)}, f)
        selected = [('frozen', RetrievalConfig.from_value(manifest['frozen_config']))]
    else:
        selected = [(c.version,c) for c in fusion_grid()] if grid else configs()
    records = []
    for name, config in selected:
        engine = ResearchEngine(retrieval_config=config)
        for row in rows:
            at = row.get('as_of') or manifest.get('as_of')
            if not at:
                raise ValueError('Freeze knowledge cutoff before evaluation')
            packet = engine.build(row['ticker'], row['question'], at, {'use_dense':dense})
            candidates = engine.last_ranking_audit['candidates']
            records.append(dict(question_id=row['id'], configuration=name, config=asdict(config),
                config_version=config.version, packet_fingerprint=packet['fingerprints'],
                ranking=[x['chunk_id'] for x in candidates], selected=[x['chunk_id'] for x in packet['results']],
                metrics=metrics(row,[x['chunk_id'] for x in packet['results']]),
                candidate_metrics=metrics(row,[x['chunk_id'] for x in candidates],100)))
    result = dict(status='measured_requires_review', winner='not_determined', manifest_sha256=digest(manifest), records=records)
    (output/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--draft',action='store_true');p.add_argument('--input',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--split',default='development',choices=['development','validation','sealed_test'])
    p.add_argument('--no-dense',action='store_true');p.add_argument('--grid',action='store_true');p.add_argument('--unseal-once',action='store_true')
    a=p.parse_args()
    if a.draft:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:json.dump(draft_benchmark(),f,ensure_ascii=False,indent=2)
        print('120 draft questions saved locally; human review pending')
    else:
        result=run(json.loads(a.input.read_text(encoding='utf-8')),a.output,split=a.split,dense=not a.no_dense,grid=a.grid,sealed=a.unseal_once)
        print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
