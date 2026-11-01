"""Production front-loaded research: one company snapshot, then industry news.

No model can invoke a second generic company search after news acquisition.
News metadata is never promoted to a verified event. This module has no writes
to Neo4j, trading API, brokerage, or remote server.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from aliases import resolve_company
from common import ROOT, dump, parse_time
from lexical import freeze
from integration.industry_context import build_industry_context, plan_search_tasks
from integration.industry_news import search_yahoo, fetch_article


DEFAULT_BUDGET = dict(max_queries=2, results_per_query=3, max_body_fetches=2,
    article_chars=18000, context_chars=24000, output_tokens=1800,
    max_findings=4, lookback_days=30)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _limits(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULT_BUDGET)):
        raise ValueError('unknown frozen budget fields')
    limits = {**DEFAULT_BUDGET, **(value or {})}
    for key, maximum in DEFAULT_BUDGET.items():
        if type(limits[key]) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError('budget exceeds frozen limit: ' + key)
    return limits


def _clock(as_of, temporal_context=None):
    at, historical = freeze(as_of)
    if temporal_context is not None:
        if not isinstance(temporal_context, dict) or type(temporal_context.get('historical')) is not bool:
            raise ValueError('historical must be boolean in the frozen clock')
        fixed = parse_time(temporal_context['analysis_time'])
        if as_of == 'current':
            if temporal_context['historical'] or fixed > at:
                raise ValueError('invalid current clock')
        elif fixed != at:
            raise ValueError('explicit analysis time and frozen clock mismatch')
        at, historical = fixed, temporal_context['historical']
    return at, historical


def _matching_background(context, ticker, question, at, historical):
    if not isinstance(context, dict):
        raise ValueError('frozen background context must be an object')
    if (context.get('research_symbol', context.get('ticker')) != ticker
            or context.get('ticker', ticker) != ticker or context.get('question') != question):
        raise ValueError('frozen background company/question mismatch')
    try:
        matched = parse_time(context['analysis_time']) == at
    except (KeyError, TypeError, ValueError, AttributeError):
        matched = False
    if not matched or type(context.get('historical')) is not bool or context['historical'] != historical:
        raise ValueError('frozen background clock/historical mismatch')
    expected_mode = 'strict_historical' if historical else 'current_as_collected'
    if context.get('temporal_mode') != expected_mode:
        raise ValueError('frozen background context temporal mode mismatch')
    return deepcopy(context)


def _snapshot_fingerprints(prepared):
    return dict(industry_context=fingerprint(prepared.get('industry_context')),
        tasks=fingerprint(prepared.get('search_tasks')),
        news_bodies=fingerprint([a.get('body_sha256') for a in prepared.get('articles', [])]),
        prepared_snapshot=fingerprint({k: v for k, v in prepared.items() if k != 'fingerprints'}))


def _validate_prepared(prepared, ticker, question, arm, as_of, temporal_context, budget):
    if (not isinstance(prepared, dict) or prepared.get('ticker') != ticker
            or prepared.get('question') != question or prepared.get('arm') != arm):
        raise ValueError('Frozen prepared context does not match run')
    at, historical = _clock(as_of, temporal_context)
    try:
        fixed = parse_time(prepared['analysis_time'])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Invalid prepared analysis time') from None
    if (type(prepared.get('historical')) is not bool or prepared['historical'] != historical
            or (as_of == 'current' and temporal_context is None and fixed > at)
            or ((as_of != 'current' or temporal_context is not None) and fixed != at)):
        raise ValueError('Frozen prepared clock/historical mismatch')
    expected_mode = 'strict_historical' if historical else 'current_as_collected'
    if prepared.get('availability_mode') != expected_mode:
        raise ValueError('Frozen prepared historical availability mismatch')
    limits = _limits(prepared.get('budget'))
    if prepared.get('budget') != limits or (budget is not None and limits != _limits(budget)):
        raise ValueError('Frozen prepared budget mismatch')
    context = prepared.get('industry_context')
    if arm == 'A' and context is not None:
        raise ValueError('A cannot receive company background context')
    if arm == 'B':
        _matching_background(context, ticker, question, fixed, historical)
    stored = prepared.get('fingerprints', {})
    if any(stored.get(key) != value for key, value in _snapshot_fingerprints(prepared).items()):
        raise ValueError('Frozen prepared snapshot fingerprint mismatch')


def _article_window(article, task, cutoff):
    """Qualify original-page time, never the provider's unverified timestamp."""
    try:
        window = task['time_window']
        begin, end = parse_time(window['start']), min(parse_time(window['end']), cutoff)
        published = parse_time(article['published_at'])
        valid = begin <= published <= end
        reason = None if valid else 'original_publication_outside_search_window'
    except (KeyError, TypeError, ValueError, AttributeError):
        valid, reason = False, 'original_publication_or_search_window_unknown'
    article['source_window_eligible'] = valid
    if not valid:
        article['eligible'] = False
        article['unknowns'] = list(dict.fromkeys([*article.get('unknowns', []), reason]))
        article['window_rejection_reason'] = reason


def prepare_research(ticker, question, as_of='current', *, arm='B', backend='local',
                     use_dense=False, fetch_news=True, search_provider=None,
                     archive_dir=None, temporal_context=None, budget=None, engine=None,
                     frozen_industry_context=None, article_provider=None, frozen_search_tasks=None):
    started = time.perf_counter()
    if arm not in ('A', 'B'): raise ValueError('arm must be A or B')
    company = resolve_company(ticker); at, historical = _clock(as_of, temporal_context)
    clock = dict(analysis_time=at.isoformat(), historical=historical)
    limits = _limits(budget)
    if arm == 'A' and frozen_industry_context is not None:
        raise ValueError('A cannot receive frozen company background context')
    directory = Path(archive_dir or ROOT/'output/industry_runtime'/('PRE5_'+uuid.uuid4().hex))
    directory.mkdir(parents=True, exist_ok=True)
    trace = []
    context = None
    if arm == 'B':
        if frozen_industry_context is not None:
            context = _matching_background(frozen_industry_context, company['research_symbol'],
                                           question, at, historical)
            trace.append(dict(phase='frontloaded_company_rag_reused_snapshot',
                              context_sha256=fingerprint(context)))
        else:
            context = build_industry_context(ticker, question, as_of,
                {'use_dense': use_dense, 'max_total_chars': 14000}, backend=backend,
                config=ROOT/'.env' if backend == 'neo4j' else None, engine=engine,
                temporal_context=clock)
            trace.append(dict(phase='frontloaded_company_rag', context_sha256=fingerprint(context)))
    tasks = plan_search_tasks(ticker, question, at.isoformat(), context,
                             max_queries=limits['max_queries'], lookback_days=limits['lookback_days'])
    if frozen_search_tasks is not None:
        if not isinstance(frozen_search_tasks,list) or not 1<=len(frozen_search_tasks)<=limits['max_queries']:
            raise ValueError('Explicit source tasks exceed the same query budget')
        for task in frozen_search_tasks:
            if (not isinstance(task.get('query_en'),str) or not task['query_en'].strip()
                    or len(task['query_en'])>500 or not task.get('task_id') or not task.get('query_hash')):
                raise ValueError('Invalid explicit source task')
            if parse_time(task['time_window']['end'])!=at:raise ValueError('Source task cutoff differs')
            if parse_time(task['time_window']['start'])<at-timedelta(days=limits['lookback_days']):
                raise ValueError('Source task window exceeds frozen bounds')
            if not set(task.get('background_evidence_ids',[]))<=set((context or {}).get('citation_map',{})):
                raise ValueError('Source task invents company background')
        tasks=deepcopy(frozen_search_tasks)
        trace.append(dict(phase='explicit_bounded_source_followup_tasks',not_original_discovery_AB=True))
    trace.append(dict(phase='industry_search_tasks', task_ids=[t['task_id'] for t in tasks]))
    searches, articles, candidates = [], [], []
    provider = search_provider or search_yahoo
    if historical or not fetch_news:
        trace.append(dict(phase='news_discovery_skipped', reason='strict_historical_no_live_vintage' if historical else 'fetch_news_not_requested'))
    else:
        for task in tasks:
            response = provider(task, at.isoformat(), historical=False,
                max_results=limits['results_per_query'], cache_dir=directory/'search_cache')
            searches.append(dict(task_id=task['task_id'], **response))
            for candidate in response.get('items', [])[:limits['results_per_query']]:
                candidates.append({**candidate, 'search_task_id': task['task_id']})
        trace.append(dict(phase='industry_news_search', query_count=len(searches)))
        seen = set()
        # One distinct candidate per topic per round, preserving relevance
        # within each query. Failed bodies consume the same bounded attempts.
        queues = {task['task_id']: [c for c in candidates if c['search_task_id'] == task['task_id']]
                  for task in tasks}
        while len(articles) < limits['max_body_fetches'] and any(queues.values()):
            for task in tasks:
                queue = queues[task['task_id']]
                candidate = None
                while queue:
                    current = queue.pop(0); key = current.get('url')
                    if key and key not in seen:
                        candidate = current; seen.add(key); break
                if candidate is None:
                    continue
                article = deepcopy((article_provider or fetch_article)(candidate, at.isoformat(), historical=False,
                    archive_dir=directory/'articles', max_chars=limits['article_chars']))
                if article.get('body_acquired') and (not article.get('published_at') or
                    (article.get('modified_at') and parse_time(article['modified_at'])<parse_time(article['published_at']))):
                    from integration.news_dates import reverify_header_date
                    article=reverify_header_date(article,at.isoformat(),task['time_window']['start'])
                article['search_task_id'] = candidate['search_task_id']
                _article_window(article, task, at)
                articles.append(article)
                if len(articles) >= limits['max_body_fetches']: break
        trace.append(dict(phase='news_body_acquisition', attempts=len(articles)))
    evidence = {}
    for article in articles:
        if not (article.get('eligible') and article.get('body_acquired') and article.get('source_verified')): continue
        for paragraph in article.get('paragraphs', []):
            cid = paragraph.get('citation_id')
            if not cid: continue
            evidence[cid] = {**paragraph, **{k: article.get(k) for k in (
                'title', 'url', 'publisher', 'published_at', 'first_retrieved_at',
                'event_time', 'body_sha256', 'version_id', 'raw_path',
                'version_scope', 'historical_eligible', 'as_of_eligible', 'modified_at',
                'raw_sha256','published_date','publication_precision','actual_public_at',
                'publication_interval','availability_basis','exact_publication_time_unknown',
                'source_clock_chronology_conflict','observed_original_time_metadata')},
                'evidence_kind': 'verified_news_body', 'round_trip_verified': True}
    unknowns = list((context or {}).get('unknowns', []))
    if not evidence: unknowns.append('No eligible verified news body; titles/summary candidates cannot establish events.')
    prepared = dict(schema_version='industry_research_prepared_v5', company=company,
        ticker=company['research_symbol'], question=question, arm=arm, **clock,
        availability_mode='strict_historical' if historical else 'current_as_collected',
        industry_context=context, search_tasks=tasks, searches=searches,
        news_candidates=candidates, articles=articles, evidence_map=evidence,
        status='partial' if evidence else 'insufficient', unknowns=list(dict.fromkeys(unknowns)),
        company_rag_calls_after_news=0, phase_trace=trace, budget=limits,
        permissions=['research_read_only'], seconds=round(time.perf_counter()-started, 4),
        temporal_limit='Current retrieved pages are collected snapshots, not verified historical versions.',
        fingerprints={})
    prepared['fingerprints'] = _snapshot_fingerprints(prepared)
    dump(directory/'prepared.json', prepared)
    return prepared


def read_news_source(prepared, citation_id):
    """Read one already acquired paragraph. Never fetch RAG or arbitrary paths."""
    record = prepared.get('evidence_map', {}).get(citation_id)
    if not record: return dict(status='insufficient', reason='citation_not_in_frozen_news')
    return dict(status='verified_snapshot_paragraph', citation=deepcopy(record),
                version_scope=record.get('version_scope'),
                current_revision_check_performed=False)


def _direct_relationship_issue(link, refs, bgrefs, evidence, context):
    """Bind a direct label to cited original evidence, never to vector relevance.

    Substring/typed-ID checks are conservative boundaries. Semantic review of
    the complete statement is still model-assisted and is not a causal proof.
    """
    category = link.get('relationship_category')
    if category not in ('direct_supply', 'direct_customer', 'customers'):
        return None
    context = context or {}
    rid = link.get('relationship_id')
    if rid:
        row = context.get('assertion_map', {}).get(rid)
        if not isinstance(row, dict):
            return 'direct_relationship_assertion_unavailable'
        direct = str(row.get('directness', '')).startswith('direct_')
        allowed = row.get('category') in ('supply', 'customer') and direct
        categories = context.get('relationship_categories', {})
        membership = categories.get('direct_supply' if category == 'direct_supply' else 'customers', [])
        allowed = allowed and rid in membership
        if (not allowed or row.get('evidence_class') != 'original_disclosure'
                or row.get('fact_edge_eligible') is not True):
            return 'direct_relationship_category_mismatch'
        needed = row.get('citation_ids', [])
        visible = context.get('citation_map', {})
        if (not isinstance(needed, list) or not needed or not set(needed) <= set(bgrefs)
                or any(cid not in visible for cid in needed)):
            return 'direct_relationship_citation_mismatch'
        for name, endpoint in (('relation_subject', 'subject_id'), ('relation_object', 'object_id')):
            if link.get(name):
                entity = context.get('entity_map', {}).get(row.get(endpoint), {})
                options = [row.get(endpoint), entity.get('name'), *entity.get('aliases', [])]
                if str(link[name]).casefold() not in [str(v).casefold() for v in options if v]:
                    return 'direct_relationship_endpoint_mismatch'
        return None
    quote = link.get('relation_quote')
    if not isinstance(quote, str) or not quote.strip():
        return 'direct_relationship_evidence_missing'
    if not any(quote in evidence.get(cid, {}).get('text', '') for cid in refs):
        return 'direct_relationship_quote_not_in_cited_news'
    def occurs(value,text):
        return re.search(r'(?<![a-z0-9])'+re.escape(value)+r'(?![a-z0-9])',text,re.I)
    def declared_endpoint(endpoint):
        if occurs(endpoint,quote):return True
        # Resolve only an abbreviation explicitly introduced in the same
        # cited paragraph, never model knowledge or vector similarity.
        full=re.sub(r'\s*\([A-Z0-9.-]{2,12}\)\s*$','',endpoint).strip()
        for cid in refs:
            paragraph=evidence.get(cid,{}).get('text','')
            pattern=r'(?<![a-z0-9])'+re.escape(full)+r'(?![a-z0-9])[^.!?\n]{0,80}?\(([A-Z0-9.-]{2,12})\)'
            for match in re.finditer(pattern,paragraph,re.I):
                if occurs(match.group(1),quote):return True
        return False
    for name in ('relation_subject', 'relation_object'):
        endpoint = link.get(name)
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint.strip()
                or not declared_endpoint(endpoint)):
            return 'direct_relationship_endpoint_mismatch'
    relation_words = r'(?:suppl(?:y|ies|ied|ying|iers?)|customers?|purchas(?:e|es|ed|ing)|procurement|sells?|sold|buys?|bought|contracts?|provid(?:e|es|ed|ing) manufacturing service)'
    denied = (re.search(r'\b(?:not|no|never|without|cannot|denied|denies)\b.{0,50}\b' + relation_words + r'\b', quote, re.I)
              or re.search(r'(?:不|未|无|不能|并非).{0,12}(?:供货|供应|采购|客户|合同|订单)', quote))
    if denied:
        return 'direct_relationship_quote_negated'
    positive = re.search(r'\b' + relation_words + r'\b|供货|供应商|供应协议|采购|客户|代工|为.{1,30}制造', quote, re.I)
    technical = re.search(r'technical|collaborat|compatib|技术合作|兼容', quote, re.I)
    action = re.search(r'\b(?:supplies|supplied|supplying|purchases|purchased|buys|bought|sells|sold)\b|供货|采购|客户|代工', quote, re.I)
    if not positive or (technical and not action):
        return 'direct_relationship_quote_not_explicit'
    return None


def audit_findings(research, reviewer, evidence, industry_context):
    """Check IDs, typed relations and inheritance; semantic review stays explicit.

    Unlike the older exact-quote gate, a supported paraphrase is not rejected
    solely because it is not a verbatim quote. A reviewer is model assistance,
    not an independent factual or causal proof.
    """
    safe, issues, warnings = [], [], []
    background = (industry_context or {}).get('citation_map', {})
    decisions = {r.get('finding_id'): r for r in reviewer.get('reviews', []) if isinstance(r, dict)}
    for original in research.get('findings', [])[:4]:
        if not isinstance(original, dict): issues.append(dict(code='invalid_finding_schema')); continue
        fid = original.get('finding_id'); review = decisions.get(fid)
        if not review or review.get('decision') not in ('retain', 'revise'):
            issues.append(dict(code='review_not_retained', finding_id=fid)); continue
        chosen = review.get('revised_finding') if review.get('decision') == 'revise' else original
        if not isinstance(chosen, dict): issues.append(dict(code='invalid_revision', finding_id=fid)); continue
        chosen = deepcopy(chosen)
        codes = []
        for field in ('conditions', 'unknowns'):
            inherited, rewritten = original.get(field, []), chosen.get(field, [])
            if (not isinstance(inherited, list) or not isinstance(rewritten, list)
                    or any(not isinstance(s, str) for s in inherited + rewritten)):
                codes.append('invalid_' + field + '_schema'); continue
            chosen['inherited_' + field] = list(dict.fromkeys(inherited))
            if any(value not in rewritten for value in inherited):
                warnings.append(dict(code='review_restored_' + field, finding_id=fid,
                    reason='Original required information retained alongside reviewer wording; semantic equivalence is not assumed.'))
            chosen[field] = list(dict.fromkeys(inherited + rewritten))
        refs = chosen.get('event_evidence_ids', [])
        if not isinstance(refs, list) or not refs or any(x not in evidence for x in refs): codes.append('unavailable_news_evidence')
        link = chosen.get('company_link') or {}
        linkrefs = link.get('event_evidence_ids', [])
        if not isinstance(linkrefs,list) or any(x not in evidence for x in linkrefs):
            codes.append('unavailable_relationship_news_evidence')
            linkrefs=[]
        chosen['relationship_evidence_ids']=list(dict.fromkeys(linkrefs))
        relation_refs=list(dict.fromkeys([*(refs if isinstance(refs,list) else []),*linkrefs]))
        if link.get('relation_quote') and not any(link['relation_quote'] in evidence.get(cid,{}).get('text','') for cid in relation_refs):
            codes.append('relationship_quote_not_in_cited_news')
        bgrefs = link.get('background_evidence_ids', [])
        if not isinstance(bgrefs, list) or any(x not in background for x in bgrefs): codes.append('unavailable_background_evidence')
        news_text = ' '.join(evidence.get(x, {}).get('text', '') for x in refs)
        models = lambda text: set(re.findall(r'\b(?:HBM\d+[A-Z0-9]*|SOCAMM\d+|CoWoS-[SRL])\b', text, re.I))
        if {x.upper() for x in models(chosen.get('event', ''))} - {x.upper() for x in models(news_text)}:
            codes.append('event_product_model_not_in_news')
        if chosen.get('basis') != 'original_fact' and not chosen.get('conditions'):
            codes.append('missing_mechanism_conditions')
        relationship_issue = _direct_relationship_issue(link, relation_refs, bgrefs, evidence, industry_context)
        if relationship_issue:
            codes.append(relationship_issue)
        positive = ' '.join(str(chosen.get(k, '')) for k in ('event', 'mechanism'))
        if re.search(r'(?:guaranteed|必然|必定|保证).{0,18}(?:利润|收益|上涨|profit|return)', positive, re.I):
            codes.append('unjustified_profit_or_return_certainty')
        if codes:
            issues.extend(dict(code=c, finding_id=fid) for c in codes); continue
        safe.append({**chosen, 'review_reason': review.get('reason'),
                     'evidence_validation': 'IDs_and_scope_checked_semantics_model_assisted',
                     'relationship_is_established_fact': chosen.get('basis') == 'original_fact'
                         and link.get('relationship_category') in ('direct_supply', 'direct_customer', 'customers')})
    # Models sometimes place descriptive background claims inside "unknowns".
    # Preserve that prose, but never let an unverified field bypass the finding
    # contract. Known tool gaps and retained finding unknowns remain available.
    model_notes=list(dict.fromkeys([*research.get('unknowns', []), *reviewer.get('unknowns', [])]))
    host_gaps=list((industry_context or {}).get('unknowns', []))
    host_gaps += [u for f in safe for u in f.get('unknowns', [])]
    if not evidence:host_gaps.append('No eligible verified recent news paragraph was supplied.')
    if not safe:host_gaps.append('No event finding was retained; this does not prove that no real event exists.')
    return dict(findings=safe, unknowns=list(dict.fromkeys(host_gaps)),
        model_gap_notes_unverified=[dict(text=n, fact_promotion_allowed=False,
            mentioned_citation_ids=re.findall(r'\b(?:C|N)_[A-Za-z0-9_]+',n),
            status='unverified_model_gap_or_background_note') for n in model_notes],
        issues=issues, warnings=warnings,
        answer_status='partial' if safe else 'insufficient',
        semantic_review_is_model_assisted_not_proof=True, execution_allowed=False)


def run_industry_research(ticker, question, as_of='current', *, backend='local',
    model_mode='controlled', llm_config_path=None, use_dense=False, fetch_news=False,
    event=None, bad_rewrite=False, arm='B', archive_dir=None, search_provider=None,
    temporal_context=None, budget=None, budget_ledger=None, prepared=None,
    run_id=None, workflow='brief', article_provider=None, frozen_industry_context=None,
    frozen_search_tasks=None):
    """Normal production entry and the shared A/B entry; no hidden paid retry."""
    if workflow not in ('brief', 'multiagent'): raise ValueError('workflow must be brief or multiagent')
    run_id = run_id or 'IND5_'+uuid.uuid4().hex
    directory = Path(archive_dir or ROOT/'output/industry_runtime'/run_id)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/'result.json').exists(): raise ValueError('Run exists; refusing duplicate model submission')
    if prepared is None:
        prepared = prepare_research(ticker, question, as_of, arm=arm, backend=backend,
            use_dense=use_dense, fetch_news=fetch_news or model_mode == 'online',
            search_provider=search_provider, archive_dir=directory,
            temporal_context=temporal_context, budget=budget,
            article_provider=article_provider,frozen_industry_context=frozen_industry_context,
            frozen_search_tasks=frozen_search_tasks)
    else:
        _validate_prepared(prepared, resolve_company(ticker)['research_symbol'], question, arm,
                           as_of, temporal_context, budget)
    if event is not None:
        prepared = deepcopy(prepared)
        prepared['news_candidates'].append({'metadata':event, 'body_acquired':False,
                                             'source_verified':False, 'reason':'external_metadata_not_evidence'})
    if model_mode=='online' and not prepared['evidence_map']:
        # The real v5 calibration consumed 12 calls without a usable body.
        # Preserve that experiment; prevent this known wasted work in new runs.
        insufficient=dict(answer_status='insufficient',findings=[],reviews=[],
            unknowns=['No eligible verified news body; paid research/review was not invoked.'],
            output_origin='deterministic_host_preflight_not_model')
        safe=audit_findings(insufficient,insufficient,{},prepared.get('industry_context'))
        result=dict(schema_version='industry_research_v5',run_id=run_id,ticker=prepared['ticker'],
            question=question,analysis_time=prepared['analysis_time'],historical=prepared['historical'],
            arm=arm,model_mode=model_mode,workflow='frontloaded_RAG_industry_search_research_review',
            prepared=prepared,raw_research=insufficient,raw_review=deepcopy(insufficient),reviewed_output=safe,
            tool_trace=prepared['phase_trace'],model_trace=[],evidence_map={},model_context_fingerprint=None,
            run_usage=dict(model_requests=0,model_calls=0,usage_known=True,
                tokens=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0),total_input_chars=0,
                news_paragraphs_delivered=0,background_quotes_delivered=0,new_company_rag_calls_after_news=0,
                provider_failure_circuit_open=False,required_context_failure=False,currency_cost=None,
                currency_reason='No provider request; search/body/RAG costs are recorded separately.'),
            seconds=0,stop_reason='no_eligible_body_preflight',company_rag_calls_after_news=0,
            research_gate=dict(execution_allowed=False,scope='research_only_permission_not_quality_score'),
            permissions=['research_read_only'])
        dump(directory/'result.json',result)
        return result
    from integration.industry_models import IndustryModels
    models = IndustryModels(prepared, directory, mode=model_mode,
        llm_config_path=llm_config_path, budget_ledger=budget_ledger, run_id=run_id)
    if workflow == 'multiagent':
        from integration.industry_multiagent import run_multiagent
        return run_multiagent(prepared, models, directory, run_id)
    # The same actual StateGraph runs in online and controlled contract mode.
    from langgraph.graph import StateGraph, START, END
    from typing_extensions import TypedDict
    class State(TypedDict, total=False):
        research: dict
        reviewer: dict
    graph = StateGraph(State)
    graph.add_node('research', lambda state: {'research':models.invoke('research')})
    graph.add_node('reviewer', lambda state: {'reviewer':models.invoke('reviewer', state['research'])})
    graph.add_edge(START, 'research'); graph.add_edge('research', 'reviewer'); graph.add_edge('reviewer', END)
    started = time.perf_counter(); output = graph.compile().invoke({})
    safe = audit_findings(output['research'], output['reviewer'], models.delivered_news, models.delivered_background)
    result = dict(schema_version='industry_research_v5', run_id=run_id,
        ticker=prepared['ticker'], question=question, analysis_time=prepared['analysis_time'],
        historical=prepared['historical'], arm=arm, model_mode=model_mode,
        workflow='frontloaded_RAG_industry_search_research_review', prepared=prepared,
        raw_research=output['research'], raw_review=output['reviewer'], reviewed_output=safe,
        tool_trace=prepared['phase_trace'], model_trace=models.traces,
        evidence_map={**models.delivered_news, **(models.delivered_background or {}).get('citation_map', {})},
        model_context_fingerprint=models.context_sha256,
        run_usage=models.usage(), seconds=round(time.perf_counter()-started, 4),
        research_gate={'execution_allowed':False, 'scope':'research_only_permission_not_quality_score'},
        company_rag_calls_after_news=0, permissions=['research_read_only'])
    dump(directory/'result.json', result)
    models.finish('completed' if not models.failed else 'provider_or_format_incomplete')
    return result
