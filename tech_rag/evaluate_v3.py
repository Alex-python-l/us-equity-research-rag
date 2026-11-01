"""Frozen source-first v3 evaluation; never overwrites v2 reports or annotations.

The optimized engine is evaluated first. The immutable v2 code then runs in an
isolated process and reads only the resulting exact query-vector cache. One
live BGE session is shared by every mode and backend in this process.
"""
import argparse
import csv
import hashlib
import json
import time
import uuid
from pathlib import Path

from common import ROOT, dump, jsonl, load, now, parse_time, readl
from evaluate_research import (anchor_hit, annotated_span, gold_relation_checks,
                               packet_checks)
from lexical import eligible, freeze
from research import ResearchEngine

FROZEN = {
    'old24': ('questions_v2.json', '1aa12f9dc8f5f4be6c84831304c5e6a709515d67ee12fb1cf6a256bd9e5eecc2'),
    'holdout14': ('questions_v3_holdout.json', 'b546c1283b3ed4c7f7283dbf4954a77de018065f195be631c91d2988c23aafb5'),
}


def verify_digest(path, expected):
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError('Frozen source-first annotation changed: ' + Path(path).name)
    return actual


def frozen_questions():
    result = {}
    for label, (filename, digest) in FROZEN.items():
        path = ROOT / 'evaluation' / filename
        verify_digest(path, digest)
        result[label] = load(path)
    return result


def interval_coverage(start, end, intervals):
    if end <= start:
        raise ValueError('Expected a nonempty half-open interval')
    merged = []
    for lo, hi in sorted((max(start, a), min(end, b)) for a, b in intervals):
        if lo >= hi:
            continue
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return sum(b - a for a, b in merged) / (end - start)


def audit_annotations(questions, engine):
    """Source/corpus audit independent of returned retrieval output/rankings.

    Uncarded means an anchor has no interval overlap with any existing reviewed
    evidence in the same immutable source version. It is intentionally stricter
    than merely having no exact prewritten answer card for the question.
    """
    anchors = []
    hashes = {}
    raw = {}
    for question in questions:
        at, historical = freeze(question['as_of'])
        for index, anchor in enumerate(question['expected_evidence']):
            source = engine.tools.sources[anchor['source_id']]
            sid = source['source_id']
            if sid not in hashes:
                hashes[sid] = hashlib.sha256((ROOT / source['local_path']).read_bytes()).hexdigest()
                raw[sid] = readl(ROOT / source['text_path'])
            block = next(b for b in raw[sid] if (b.get('pdf_page'), b.get('paragraph')) ==
                         (anchor['pdf_page'], anchor['paragraph']))
            exact = block['text'][anchor['char_start']:anchor['char_end']] == anchor['needle']
            start, end = annotated_span(engine, anchor)
            evidence = [e for e in engine.tools.evidence.values()
                        if (e['source_id'], e.get('pdf_page'), e.get('paragraph'), e.get('source_sha256')) ==
                        (sid, anchor['pdf_page'], anchor['paragraph'], hashes[sid])
                        and e.get('review_status') == 'curated_claim_with_exact_passage_match'
                        and eligible(e, at, historical)
                        and e['char_start'] < end and e['char_end'] > start]
            fraction = interval_coverage(start, end, [(e['char_start'], e['char_end']) for e in evidence])
            evidence_ids = {e['evidence_id'] for e in evidence}
            relations = [r['relation_id'] for r in engine.tools.relations
                         if evidence_ids.intersection(r['evidence_ids'])]
            time_ok = parse_time(source['available_at']) <= at and (
                not historical or source.get('historical_eligible', False))
            anchors.append({
                'question_id': question['id'], 'anchor_index': index, 'source_id': sid,
                'pdf_page': anchor['pdf_page'], 'paragraph': anchor['paragraph'],
                'raw_interval': [anchor['char_start'], anchor['char_end']],
                'normalized_interval': [start, end], 'exact_raw_slice': exact,
                'raw_sha256': hashes[sid], 'source_sha256_matches': hashes[sid] == source['sha256'],
                'historical': historical, 'as_of': question['as_of'],
                'available_at': source['available_at'],
                'historical_eligible_at_cutoff': time_ok,
                'curated_evidence_overlap_fraction': fraction,
                'overlapping_evidence_ids': sorted(evidence_ids),
                'overlapping_relation_ids': sorted(relations),
                'no_reviewed_card_evidence_overlap': fraction == 0.,
            })
    fully = [q['id'] for q in questions if q['expected_evidence'] and all(
        a['no_reviewed_card_evidence_overlap'] for a in anchors if a['question_id'] == q['id'])]
    invalid = [a for a in anchors if not (a['exact_raw_slice'] and a['source_sha256_matches']
                                         and a['historical_eligible_at_cutoff'])]
    return {'executed_at': now(), 'method': 'raw exact slices/hash/time, then existing reviewed evidence interval overlap; no retrieval output inspected',
            'anchors_checked': len(anchors), 'invalid_anchors': invalid,
            'fully_uncarded_questions': fully, 'anchors': anchors,
            'scope': 'same source hash/page/paragraph half-open normalized Unicode intervals; no semantic assertion about completeness of all industry knowledge'}


def budget_checks(packet):
    usage = packet['budget_usage']
    budget = packet['budget']
    return [key for key, limit in [('text_chars', 'max_total_chars'),
                                  ('retrieval_tool_calls', 'max_tool_calls')]
            if usage[key] > budget[limit]]


def require_complete_batch(requests, packets):
    if len(requests) != len(packets):
        raise RuntimeError('Baseline returned an incomplete batch; refusing to omit questions')


def measure_packet(question, packet, engine, *, version, mode, backend, seconds=None):
    anchors = [anchor_hit(engine, anchor, packet['citations']) for anchor in question['expected_evidence']]
    relations = gold_relation_checks(question, packet, engine)
    return {
        'question_id': question['id'], 'set': 'holdout14' if question['id'].startswith('V3_') else 'old24',
        'ticker': question['ticker'], 'category': question['category'], 'version': version,
        'mode': mode, 'backend': backend, 'expected_status': question['expected_status'],
        'returned_status': packet['status'], 'expected_anchors': len(anchors),
        'full_anchor_hits': sum(a['full_hit'] for a in anchors),
        'any_overlap_hits': sum(a['any_overlap'] for a in anchors), 'anchors': anchors,
        'expected_relations': len(relations), 'gold_relation_hits': sum(r['found'] for r in relations),
        'gold_relationships': relations, 'returned_results': len(packet['results']),
        'returned_paths': len(packet['paths']), 'truncated': packet['coverage']['truncated'],
        'truncation': packet['coverage']['truncation'], 'stop_reason': packet['stop_reason'],
        'insufficient_reported': packet['status'] == 'insufficient',
        'unknown_case_correctly_abstained': packet['status'] == 'insufficient'
        if question['expected_status'] == 'insufficient' else None,
        'answerable_marked_insufficient': question['expected_status'] != 'insufficient'
        and packet['status'] == 'insufficient',
        'text_chars': packet['budget_usage']['text_chars'],
        'retrieval_tool_calls': packet['budget_usage']['retrieval_tool_calls'],
        'budget_violations': budget_checks(packet), 'budget': packet['budget'],
        'seconds': seconds, 'analysis_time': packet['analysis_time'],
        'fingerprints': packet['fingerprints'], **packet_checks(engine, packet),
    }


def summarize_rows(rows):
    total = sum(r['expected_anchors'] for r in rows)
    references = sum(r['citations_checked'] for r in rows)
    relations = sum(r['expected_relations'] for r in rows)
    timed = [r['seconds'] for r in rows if r['seconds'] is not None]
    return {
        'questions': len(rows), 'gold_anchors': total,
        'full_anchor_hits': sum(r['full_anchor_hits'] for r in rows),
        'full_anchor_recall': sum(r['full_anchor_hits'] for r in rows) / total if total else None,
        'any_overlap_recall': sum(r['any_overlap_hits'] for r in rows) / total if total else None,
        'citations_checked': references,
        'citation_round_trip_rate': 1 - sum(len(r['citation_errors']) for r in rows) / references if references else None,
        'gold_expected_relations': relations, 'gold_relation_hits': sum(r['gold_relation_hits'] for r in rows),
        'gold_relation_recall': sum(r['gold_relation_hits'] for r in rows) / relations if relations else None,
        'relationship_direction_or_type_errors': sum(len(r['relation_errors']) for r in rows),
        'historical_leaks': sum(len(r['historical_leaks']) for r in rows),
        'known_unknown_cases': sum(r['expected_status'] == 'insufficient' for r in rows),
        'known_unknown_abstentions': sum(r['unknown_case_correctly_abstained'] is True for r in rows),
        'insufficient_returned': sum(r['insufficient_reported'] for r in rows),
        'answerable_marked_insufficient': sum(r.get('answerable_marked_insufficient', False) for r in rows),
        'truncated_questions': sum(r['truncated'] for r in rows),
        'budget_violation_questions': sum(bool(r['budget_violations']) for r in rows),
        'text_chars_total': sum(r['text_chars'] for r in rows),
        'retrieval_tool_calls_total': sum(r['retrieval_tool_calls'] for r in rows),
        'timed_questions': len(timed), 'seconds_total': sum(timed) if timed else None,
    }


def future_corpus_stability(engine, questions):
    """Replay historical queries before/after adding real later source chunks.

    The earlier snapshot has only eligible original chunks, the later snapshot
    contains the existing full corpus. All other data is identical and still
    prefiltered; this checks citation membership AND actual returned rank order.
    """
    rows = []
    for question in questions:
        if question['as_of'] == 'current':
            continue
        at, historical = freeze(question['as_of'])
        before_chunks = [c for c in engine.chunks if eligible(c, at, historical)]
        before_engine = ResearchEngine(engine.backend, engine.config, chunks=before_chunks,
                                       index_path=':memory:', tools=engine.tools)
        before = before_engine.build(question['ticker'], question['question'], question['as_of'],
                                     question.get('budget_override'))
        after = engine.build(question['ticker'], question['question'], question['as_of'],
                             question.get('budget_override'))
        keys = lambda p: [(x['chunk_id'], x['rank']) for x in p['results']]
        row = {'question_id': question['id'], 'analysis_time': question['as_of'],
               'before_eligible_chunks': len(before_chunks), 'after_all_chunks': len(engine.chunks),
               'later_or_unverified_chunks_added': len(engine.chunks) - len(before_chunks),
               'returned_rank_order_unchanged': keys(before) == keys(after),
               'citation_ids_unchanged': before['citations'] == after['citations'],
               'assertion_ids_unchanged': set(before['assertion_map']) == set(after['assertion_map']),
               'eligible_data_fingerprint_unchanged': before['fingerprints']['eligible_data'] == after['fingerprints']['eligible_data'],
               'eligible_keyword_fingerprint_unchanged': before['fingerprints']['eligible_keyword_corpus'] == after['fingerprints']['eligible_keyword_corpus']}
        row['pass'] = all(row[k] for k in ['returned_rank_order_unchanged', 'citation_ids_unchanged',
                                          'assertion_ids_unchanged', 'eligible_data_fingerprint_unchanged',
                                          'eligible_keyword_fingerprint_unchanged'])
        rows.append(row)
    return rows


def finalize_existing_report(backend):
    """Derive diagnostics from saved real runs, without executing retrieval/model.

    This also upgrades early v3 reports with the explicit answerable/insufficient
    diagnostic. Source packet content and every measured hit remain unchanged.
    """
    path = ROOT / 'output' / ('v3_evaluation_' + backend + '.json')
    report = load(path)
    groups = frozen_questions()
    expected_rows = sum(len(qs) for qs in groups.values()) * 3 * 2
    require_complete_batch(range(expected_rows), report['rows'])
    for row in report['rows']:
        row['answerable_marked_insufficient'] = row['expected_status'] != 'insufficient' and row['returned_status'] == 'insufficient'
    report['summary'] = {group: {version: {mode: summarize_rows([
        r for r in report['rows'] if r['set'] == group and r['version'] == version and r['mode'] == mode
    ]) for mode in ['graph', 'dense', 'unified']} for version in ['baseline', 'optimized']}
        for group in groups}
    report['failures'] = [r for r in report['rows'] if r['full_anchor_hits'] < r['expected_anchors']
        or r['gold_relation_hits'] < r['expected_relations'] or r['citation_errors']
        or r['relation_errors'] or r['historical_leaks'] or r['budget_violations']
        or r['unknown_case_correctly_abstained'] is False or r['answerable_marked_insufficient']]
    report['diagnostics_derived_at'] = now()
    report['diagnostics_derivation'] = 'saved real packet metrics only; no re-retrieval, no gold changes, no model initialization'
    dump(path, report)
    text = [f'V3 检索对照实测：{backend}',
            '固定旧24题/55锚点 + 独立留出14题/32锚点；每题 graph/dense/unified，冻结旧代码与优化版配置预算完全相同。',
            '原文锚点：相同资料SHA/页码/段落，全部独立标注区间被引用并集完整覆盖才算命中。',
            '多轮示例不混入单包召回。引用回查正确率不是答案准确率。', '']
    for group in groups:
        for version in ['baseline', 'optimized']:
            values = report['summary'][group][version]
            text.append(group + ' ' + version + ': ' + ', '.join(
                mode + ' ' + str(values[mode]['full_anchor_hits']) + '/' + str(values[mode]['gold_anchors'])
                for mode in ['graph', 'dense', 'unified']))
    text.extend(['', '保留的失败与边界：'])
    for row in report['rows']:
        if row['version'] != 'optimized' or row['mode'] != 'unified':
            continue
        missing = [str(i) for i, a in enumerate(row['anchors']) if not a['full_hit']]
        if missing or row['unknown_case_correctly_abstained'] is False or row['answerable_marked_insufficient']:
            text.append(f"{row['question_id']}: {row['full_anchor_hits']}/{row['expected_anchors']}, "
                        f"未完整锚点索引={','.join(missing) or '无'}, status={row['returned_status']}, "
                        f"expected={row['expected_status']}, stop={row['stop_reason']}, "
                        f"截断={row['truncated']}, chars={row['text_chars']}, calls={row['retrieval_tool_calls']}。")
    text.extend(['', 'H03 留出题统一检索1/3→0/3，未通过修改算法或删除问题掩盖退化。',
                 'H04/H08 未披露供应商细节、匿名间接客户两题均未返回 insufficient。当前 partial 标签不能当成正确拒答。',
                 'H01 原文锚点虽完整命中，检索状态仍是 insufficient，说明状态判断和引用召回需分别评估。',
                 'H02/H03/H04/H07/H14 等锚点未完整命中；原文已归档，不能解释成资料不存在，也不能用相似段落替代独立锚点。',
                 '目前只能确认排名/候选/组装或预算导致可见证据不足；未以此评测反推新gold或逐题白名单。',
                 '单包全部38个统一检索问题仍报告截断；因此没有声称检索覆盖完备。',
                 'CoWoS 多轮单列：5/5锚点、5轮、8次调用、3580字符，停止于page_limit；与单包不可混算。',
                 '7个历史问题在仅有截止前原文与加入现有后续原文后，允许的引用、结果顺序和可用语料指纹全部相同。',
                 '所有已返回引用回查率100%，结构方向/类型错0、历史泄漏0；关系检查只对照审核目录，并非独立外部事实精度。',
                 '没有生成LLM自然语言答案，没有计算股票信号、真实因子或收益，没有写入数据库。',
                 '旧版批量耗时包括子进程和反序列化；优化版首次dense耗时包括一次模型加载，不能据此作对称速度比较。',
                 '后续优化若使用这些留出题诊断，应保留本次退化报告并另建未见题集验证泛化。'])
    (ROOT / 'output' / ('v3_evaluation_' + backend + '_analysis.txt')).write_text('\n'.join(text) + '\n', encoding='utf-8')
    return report


def run(backend='local', config=None, *, agent_examples=True):
    from compare_retrieval_v3 import baseline_packets
    groups = frozen_questions()
    questions = groups['old24'] + groups['holdout14']
    started = time.perf_counter()
    engine = ResearchEngine(backend, config)
    out = ROOT / 'output' / ('v3_evaluation_' + backend)
    out.mkdir(exist_ok=True)
    # This audit is serialized BEFORE any question is run; gold is never changed.
    audit = audit_annotations(groups['holdout14'], engine)
    dump(ROOT / 'output' / ('v3_holdout_audit_' + backend + '.json'), audit)
    if audit['invalid_anchors'] or len(audit['fully_uncarded_questions']) < 3:
        raise RuntimeError('Source-first annotation audit failed; do not evaluate/relabel silently')
    rows, requests, metadata, packets = [], [], [], []
    for question in questions:
        for mode in ['graph', 'dense', 'unified']:
            request = {'ticker': question['ticker'], 'question': question['question'],
                       'as_of': question['as_of'], 'budget': question.get('budget_override'),
                       'mode': mode, 'backend': backend, 'config': str(config) if config else None}
            before = time.perf_counter()
            packet = engine.build(question['ticker'], question['question'], question['as_of'],
                                  question.get('budget_override'), mode=mode)
            elapsed = time.perf_counter() - before
            row = measure_packet(question, packet, engine, version='optimized', mode=mode,
                                 backend=backend, seconds=elapsed)
            rows.append(row)
            packets.append(packet)
            requests.append(request)
            metadata.append((question, mode))
            dump(out / (question['id'] + '_' + mode + '_optimized.json'), packet)
            print(json.dumps({k: row[k] for k in ['question_id', 'version', 'mode', 'full_anchor_hits',
                                                  'expected_anchors', 'seconds']}, ensure_ascii=False), flush=True)
    before = time.perf_counter()
    old_packets = baseline_packets(requests, timeout=900)
    require_complete_batch(requests, old_packets)
    baseline_seconds = time.perf_counter() - before
    comparisons = []
    for (question, mode), old, new in zip(metadata, old_packets, packets):
        row = measure_packet(question, old, engine, version='baseline', mode=mode, backend=backend)
        rows.append(row)
        dump(out / (question['id'] + '_' + mode + '_baseline.json'), old)
        old_anchors = [a['full_hit'] for a in row['anchors']]
        new_anchors = [anchor_hit(engine, a, new['citations'])['full_hit'] for a in question['expected_evidence']]
        comparisons.append({'question_id': question['id'], 'mode': mode,
                            'set': row['set'], 'baseline_hits': sum(old_anchors), 'optimized_hits': sum(new_anchors),
                            'delta': sum(new_anchors) - sum(old_anchors), 'expected_anchors': len(old_anchors),
                            'lost_anchor_indices': [i for i, (a, b) in enumerate(zip(old_anchors, new_anchors)) if a and not b],
                            'gained_anchor_indices': [i for i, (a, b) in enumerate(zip(old_anchors, new_anchors)) if b and not a],
                            'equal_budget': old['budget'] == new['budget'],
                            'equal_eligible_corpus': old['fingerprints']['eligible_keyword_corpus'] == new['fingerprints']['eligible_keyword_corpus']})
    summaries = {group: {version: {mode: summarize_rows([r for r in rows if r['set'] == group
        and r['version'] == version and r['mode'] == mode]) for mode in ['graph', 'dense', 'unified']}
        for version in ['baseline', 'optimized']} for group in groups}
    multi = []
    if agent_examples:
        from agent import run_agent
        for qid in ['V2_24']:
            question = next(q for q in questions if q['id'] == qid)
            before = time.perf_counter()
            answer = run_agent(question['ticker'], question['question'], question['as_of'],
                               'v3_eval_' + backend + '_' + uuid.uuid4().hex, backend, config,
                               budget=question['budget_override'], max_rounds=5, engine=engine,
                               refresh_news=False)
            packet = answer['evidence_packet']
            measured = measure_packet(question, packet, engine, version='optimized', mode='agent_multi',
                                      backend=backend, seconds=time.perf_counter() - before)
            measured.update(rounds_used=answer['rounds_used'], agent_stop_reason=answer['stop_reason'],
                            comparison_scope='separate multi-round example; not added to single-packet recall')
            multi.append(measured)
            dump(out / (qid + '_agent_multi_optimized.json'), answer)
    stability = future_corpus_stability(engine, questions)
    failures = [r for r in rows if r['full_anchor_hits'] < r['expected_anchors']
                or r['gold_relation_hits'] < r['expected_relations'] or r['citation_errors']
                or r['relation_errors'] or r['historical_leaks'] or r['budget_violations']
                or r['unknown_case_correctly_abstained'] is False
                or r['answerable_marked_insufficient']]
    report = {
        'executed_at': now(), 'backend': backend, 'frozen_question_sets': FROZEN,
        'annotation_method': 'independent source-first automated annotations frozen before retrieval; no human subject-matter adjudication',
        'all_question_budgets_equal': all(c['equal_budget'] for c in comparisons),
        'all_eligible_keyword_corpora_equal': all(c['equal_eligible_corpus'] for c in comparisons),
        'baseline_execution': 'isolated immutable source snapshot, exact existing cached query vectors, no model initialized in worker',
        'baseline_batch_seconds': baseline_seconds, 'optimized_single_packet_seconds': sum(r['seconds'] for r in rows if r['seconds'] is not None),
        'total_seconds': time.perf_counter() - started, 'summary': summaries, 'rows': rows,
        'comparisons': comparisons, 'regressions': [c for c in comparisons if c['delta'] < 0],
        'failures': failures, 'agent_examples': multi, 'future_corpus_stability': stability,
        'all_future_corpus_stability_checks_pass': all(r['pass'] for r in stability),
        'fully_uncarded_holdout_questions': audit['fully_uncarded_questions'],
        'metric_definitions': {
            'anchor_recall': 'full union coverage of independently marked normalized original intervals, exact source hash/page/paragraph; partial overlap separately reported',
            'relation_metric': 'gold triples+class recall; direction/type errors are consistency against curated assertions, not independent semantic precision',
            'time': 'optimized per-call wall time includes first model load; baseline wall time includes subprocess and batch deserialization, no per-question allocation',
            'insufficient': 'retrieval status/explicit known unknown gate; not a score of generated answers because this evaluator generates no answer',
            'ranking': 'FTS statistics recomputed on cutoff-eligible corpus before ranking; model uses frozen pre-existing learned parameters, not document retraining',
        },
        'limitations': ['38题仅为有限科技试点，留出题为独立自动标注而非产业专家金标。',
                       '关系类型/方向检查为结构及审核目录一致性；不能当作对原始事实的独立准确率。',
                       '未评估LLM自由文本判断、策略信号、实际因子或收益。',
                       '历史可用性仍采用保守available_at，全球实际首次公开时间未全部独立核验。',
                       '新旧current查询的冻结时钟可相差数秒；逐项验证可用语料指纹相同，不将时钟字面相等伪报为已测。',
                       '保留所有失败、回退和截断案例；多轮结果单列，未混入单包召回。'],
        'database_writes_executed': False,
    }
    dump(ROOT / 'output' / ('v3_evaluation_' + backend + '.json'), report)
    jsonl(out / 'rows.jsonl', rows)
    with (out / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    print(json.dumps({'backend': backend, 'summary': summaries, 'regressions': report['regressions'],
                      'future_checks_pass': report['all_future_corpus_stability_checks_pass']}, ensure_ascii=False), flush=True)
    return finalize_existing_report(backend)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', choices=['local', 'neo4j', 'both'], default='local')
    parser.add_argument('--config', default=None)
    parser.add_argument('--no-agent-examples', action='store_true')
    parser.add_argument('--summarize-existing', action='store_true',
                        help='derive diagnostics only; never initialize a model or execute retrieval')
    options = parser.parse_args()
    for selected in (['local', 'neo4j'] if options.backend == 'both' else [options.backend]):
        if options.summarize_existing:
            finalized = finalize_existing_report(selected)
            print(json.dumps({'backend': selected, 'diagnostics_derived_at': finalized['diagnostics_derived_at']}, ensure_ascii=False))
            continue
        if selected == 'neo4j' and not options.config:
            parser.error('Neo4j requires explicit --config; no fallback or automatic .env discovery')
        run(selected, options.config if selected == 'neo4j' else None,
            agent_examples=not options.no_agent_examples)
