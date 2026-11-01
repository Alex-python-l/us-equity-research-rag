"""One bounded original-evidence front load and explainable industry searches.

The planner emits search directions, never claims or an answer scaffold. Local
ResearchEngine supplies all facts/identities and preserves its original C IDs.
No news, credentials, embedding build, model download or database write occurs.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
import re
import unicodedata

from aliases import resolve_company
from common import parse_time
from lexical import freeze
from research import ResearchBudget, ResearchEngine, build_research_context


MAX_BACKGROUND_CHARS = 14000
SCHEMA = 'industry_context_v5'
PLANNER_VERSION = 'industry_search_direction_v5_1'


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _question(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        raise ValueError('question must be 1..8000 characters')
    return value


def _budget(value):
    supplied = asdict(value) if isinstance(value, ResearchBudget) else dict(value or {})
    # Engine text accounting includes both originals and reviewed relation scope.
    # Leave room for the latter instead of consuming the full cap with quotes.
    result = ResearchBudget.from_value({'use_dense':False, 'max_total_chars':MAX_BACKGROUND_CHARS,
        'max_results':8, 'max_excerpt_chars':1400, 'context_radius':0, **supplied})
    return replace(result, max_total_chars=min(result.max_total_chars, MAX_BACKGROUND_CHARS))


def _categories(assertions, symbol):
    categories = {key:[] for key in ('direct_supply', 'customers', 'technical_cooperation',
        'industry_dependency', 'inference', 'competition', 'product_design', 'other_disclosures')}
    business = []; constraints = []
    for rid, row in assertions.items():
        own = symbol in (row['subject_id'], row['object_id'])
        if row.get('evidence_class') != 'original_disclosure' or not row.get('fact_edge_eligible', True):
            categories['inference'].append(rid)
            continue
        predicate = row['predicate']; category = row.get('category')
        if own and category == 'business':
            business.append(rid)
        elif category in ('constraint', 'policy_constraint'):
            constraints.append(rid)
        elif category == 'collaboration' or predicate in ('COLLABORATES_WITH', 'CO_DEVELOPS'):
            categories['technical_cooperation'].append(rid)
        elif category == 'technical_dependency':
            categories['industry_dependency'].append(rid)
        elif category == 'supply' and row.get('directness', '').startswith('direct_') and own:
            categories['direct_supply'].append(rid)
            if row['subject_id'] == symbol:
                categories['customers'].append(rid)
        elif category == 'customer' and row['subject_id'] == symbol:
            categories['customers'].append(rid)
        elif category == 'competition':
            categories['competition'].append(rid)
        elif category == 'product_design':
            categories['product_design'].append(rid)
        else:
            categories['other_disclosures'].append(rid)
    return categories, business, constraints


def build_industry_context(ticker, question, as_of, budget=None, *, backend='local',
                           config=None, engine=None, temporal_context=None):
    """Return immutable original references plus typed, bounded industry context.

    A single unified build is the logical front load. Existing engine channels,
    local model/index/query caches and pagination are reused; partial coverage is
    returned without an automatic graph/news expansion. Pass current plus a
    nonhistorical temporal_context to share one frozen current research clock.
    """
    identity = resolve_company(ticker); symbol = identity['research_symbol']
    _question(question); bound = _budget(budget)
    if engine is None:
        # The existing convenience API owns the artifact-sensitive engine cache.
        packet = build_research_context(ticker, question, as_of, bound, backend=backend,
            config=config, temporal_context=temporal_context)
        entities = {}
    else:
        packet = engine.build(ticker, question, as_of, bound, mode='unified', temporal_context=temporal_context)
        entities = getattr(getattr(engine, 'tools', None), 'entities', {})
    packet = deepcopy(packet)
    citations = packet['citations']; assertions = packet['assertion_map']
    if sum(len(row['text']) for row in citations.values()) > bound.max_total_chars:
        raise ValueError('ResearchEngine exceeded the IndustryContext original-excerpt budget')
    categories, business, constraints = _categories(assertions, symbol)
    entity_ids = sorted({row[key] for row in assertions.values() for key in ('subject_id','object_id')})
    # Only assertion-visible identities enter the view. An absent endpoint name
    # remains an ID; unrelated/future catalog entities cannot seed new searches.
    entity_map = {key:deepcopy(entities.get(key, {'entity_id':key})) for key in entity_ids}
    versions = {}
    for citation in citations.values():
        key = citation['source_id'] + '@' + citation['source_sha256']
        versions[key] = {field:citation.get(field) for field in ('source_id','source_sha256',
            'source_url','title','report_period','available_at','historical_eligible','availability_basis',
            'actual_public_at','published_at')}
    coverage = deepcopy(packet.get('coverage', {}))
    coverage.update(original_excerpt_chars=sum(len(row['text']) for row in citations.values()),
        original_excerpt_char_cap=bound.max_total_chars, citation_count=len(citations),
        logical_front_loads=1, engine_build_calls=1, automatic_pagination=False,
        has_more=bool(packet.get('pagination', {}).get('next_cursor')), exhaustive=False)
    stable = [SCHEMA, symbol, question, packet['analysis_time'], packet['temporal_mode'],
        packet['fingerprints'], {key:{k:c.get(k) for k in ('source_id','source_sha256','pdf_page',
        'paragraph','char_start','char_end','available_at')} for key,c in citations.items()}, sorted(assertions)]
    unknowns = list(dict.fromkeys(packet.get('unknowns', []) + packet.get('insufficiency_reasons', [])))
    sections = {'business_and_products':business, 'constraints':constraints,
                **{key:categories[key] for key in ('direct_supply','customers',
                    'technical_cooperation','industry_dependency')}}
    coverage['missing_background_sections'] = [key for key, values in sections.items() if not values]
    labels = {'business_and_products':'主营业务与产品', 'constraints':'经营与政策约束',
              'direct_supply':'直接具名供货', 'customers':'客户',
              'technical_cooperation':'技术合作', 'industry_dependency':'行业依赖'}
    unknowns += ['本次前置预算内未覆盖'+labels[key]+'的可回查审核关系；不代表不存在或未披露。'
                 for key in coverage['missing_background_sections']]
    return dict(schema_version=SCHEMA, context_id='IC_'+_digest(stable)[:32],
        ticker=symbol, research_symbol=symbol, company=deepcopy(packet['company']), question=question,
        analysis_time=packet['analysis_time'], temporal_mode=packet['temporal_mode'],
        historical=packet['temporal_mode']=='strict_historical', backend=packet['backend'],
        status=packet['status'], evidence_packet=packet, citation_map=citations,
        assertion_map=assertions, evidence_map=packet['evidence_map'], entity_map=entity_map,
        business_and_products=business, company_profile=deepcopy(packet.get('company_profile', {})),
        relationship_categories=categories, constraints=constraints, paths=deepcopy(packet.get('paths', [])),
        unknowns=unknowns, source_versions=versions, fingerprints=deepcopy(packet['fingerprints']),
        coverage=coverage, budget=asdict(bound), budget_usage=deepcopy(packet.get('budget_usage', {})),
        permissions=['research_read_only'],
        interpretation_policy='Original disclosures retain scope; technical cooperation, dependency, '
            'product design, inference and vector relevance do not establish procurement or causal returns.')


# Topic vocabulary is an explanatory query router, not a company-answer table.
# The order of actual industry mentions in the user's question ranks directions.
TOPICS = (
    dict(key='memory_supply', label='HBM/DRAM有效供给与产能投产',
         pattern=r'\bHBM\d?E?\b|\bDRAM\b|high.bandwidth.memory|高带宽(?:内存|存储)',
         base='HBM DRAM memory', zh='HBM DRAM 内存',
         change='effective qualified supply and capacity expansion coming online',
         en='capacity expansion production ramp qualification supply pricing changes',
         changes_zh='有效产能 扩产投产 量产爬坡 认证 供给 价格 近期变化',
         mechanism=['合格有效供给与投产节奏','产品可交付数量、产品组合或单位成本'],
         conditions=['扩产已投产并达到对应产品所需良率与客户认证', '变化适用于目标产品与研究展望期'],
         unknowns=['实际合格产能与增量投产时间', '库存缓冲、采购数量和合同价格']),
    dict(key='memory_demand', label='AI服务器内存需求与产品组合',
         pattern=r'AI.?服务器|AI.?server|memory demand|内存需求|存储需求|product mix|产品组合',
         base='AI server memory', zh='AI服务器 内存',
         change='memory demand, deployment and product mix changes',
         en='HBM DRAM demand product mix deployment orders changes',
         changes_zh='HBM DRAM 需求 产品组合 部署 订单 近期变化',
         mechanism=['AI服务器部署与内存配置变化','对应产品需求、组合与产能分配'],
         conditions=['已确认的部署或配置变化对应实际采购需求', '产品代际、容量与交付周期能够匹配'],
         unknowns=['订单是否已确认以及是否重复统计', '内存配置、产品代际与具体交付窗口']),
    dict(key='advanced_packaging', label='先进封装及基板测试配套有效产能',
         pattern=r'CoWoS|advanced.packaging|先进封装|封装.*基板|interposer|ABF|substrate',
         base='advanced packaging substrate testing', zh='先进封装 基板 测试',
         change='qualified packaging capacity and supporting substrate/testing availability',
         en='capacity expansion qualification yield production ramp bottleneck changes',
         changes_zh='有效产能 扩产 认证 良率 投产 瓶颈 近期变化',
         mechanism=['合格封装、基板与测试配套产能','芯片或系统交付与单位封装成本'],
         conditions=['配套基板、测试、人员与设备同时可用', '封装技术路线与产品认证能够匹配'],
         unknowns=['分路线有效产能与良率', '客户分配、订单排程及替代认证时间']),
    dict(key='foundry_ramp', label='先进制程制造扩产与量产爬坡',
         pattern=r'先进制程|advanced.node|\bfoundr(?:y|ies)\b|\bwafer\b|晶圆|量产爬坡',
         base='advanced node semiconductor foundry', zh='先进制程 半导体 晶圆代工',
         change='manufacturing expansion, equipment availability and volume-production ramps',
         en='fab capacity expansion equipment yield volume production ramp changes',
         changes_zh='晶圆厂 扩产 设备 良率 量产 爬坡 近期变化',
         mechanism=['先进制程合格晶圆产出与爬坡节奏','可交付产能、单位成本与客户需求兑现'],
         conditions=['设备交付安装、人员、能源和客户认证满足量产条件', '需求与对应工艺节点及投产时间相匹配'],
         unknowns=['节点实际良率与有效产出', '客户承诺数量、产能分配及投产时间']),
    dict(key='data_center_infrastructure', label='数据中心电力冷却网络建设与投运',
         pattern=r'数据中心.*(?:电力|冷却|网络)|data.center.*(?:power|cooling|network)|液冷|电网|\bcooling\b|\belectricity\b',
         base='data center power cooling networking infrastructure', zh='数据中心 电力 冷却 网络 基础设施',
         change='construction, grid connection and commissioning of usable infrastructure',
         en='construction grid connection commissioning deployment delays changes',
         changes_zh='建设 电网接入 投运 部署 延迟 近期变化',
         mechanism=['可投运的数据中心电力、冷却与网络','算力设备部署、验收与系统交付节奏'],
         conditions=['供电、冷却、网络及客户验收均达到实际部署要求', '建设变化对应目标产品和客户交付窗口'],
         unknowns=['可交付电力与投运时间', '受影响订单、库存及客户验收条件']),
    dict(key='compute_buyer_capex', label='云服务商及算力买方资本开支与部署',
         pattern=r'资本开支|capital.spending|\bcapex\b|云服务商|cloud.provider|hyperscaler|deployment.schedule|采购方.*部署',
         base='cloud providers AI compute buyers', zh='云服务商 AI算力 采购方',
         change='capital-spending commitments, procurement and deployment schedule changes',
         en='capital spending capex procurement deployment schedule demand changes',
         changes_zh='资本开支 采购 部署 节奏 需求 近期变化',
         mechanism=['买方已确认的资本开支与部署计划','可兑现采购需求与产品系统交付'],
         conditions=['预算已转化为目标产品采购而非仅意向或土建投入', '部署配套、验收和交付时间能够匹配'],
         unknowns=['资本开支口径及是否包含非算力项目', '已确认采购、供应商分配与部署时间']),
    dict(key='storage_supply_demand', label='NAND/SSD存储供需与产品进展',
         pattern=r'\bNAND\b|\bSSD\b|SOCAMM|闪存|固态硬盘',
         base='NAND SSD memory storage', zh='NAND SSD 存储',
         change='qualified storage supply, demand, pricing and product-stage changes',
         en='capacity supply demand pricing qualification volume production changes',
         changes_zh='供给 需求 价格 认证 量产 产品进展 近期变化',
         mechanism=['存储有效供需、认证与产品阶段','产品组合、交付能力或成本'],
         conditions=['对应产品已达到明确的认证或商业化阶段', '产品型号和下游应用与目标业务相匹配'],
         unknowns=['对应型号的实际订单与供给', '价格条款、库存及产品阶段']),
    dict(key='semiconductor_policy', label='半导体出口政策与许可条件',
         pattern=r'出口管制|出口限制|export.control|export.restriction|trade.policy|政策调整',
         base='semiconductor export controls licensing', zh='半导体 出口管制 许可证',
         change='published policy, licensing conditions and implementation changes',
         en='rule license implementation official announcement changes',
         changes_zh='规则 许可 执行 正式公告 近期变化',
         mechanism=['正式规则适用范围与许可执行','受限产品交付、市场可及性与合规成本'],
         conditions=['最终正式规则明确适用于对应产品、地区与交易', '许可及过渡安排已核实'],
         unknowns=['具体许可结果与适用产品', '替代产品、需求转移及实际订单影响']),
)


def _matches(topic, text):
    return list(re.finditer(topic['pattern'], text, re.I | re.ASCII))


def _technical_tokens(topic, text):
    patterns = {
        'memory_supply':r'\bHBM[234](?:E)?\b',
        'memory_demand':r'\bHBM[234](?:E)?\b|SOCAMM\d+',
        'advanced_packaging':r'CoWoS\s*-\s*[SRL]',
        'foundry_ramp':r'\bN[2345][A-Z]?\b|\b[2345]\s*nm\b',
        'storage_supply_demand':r'SOCAMM\d+|\b\d{4}\s*SSD\b',
    }
    return list(dict.fromkeys(m.group() for m in re.finditer(patterns.get(topic['key'], r'(?!)'), text, re.I | re.ASCII)))


def _contains_token(text, token):
    # Normalize typography, preserving generation boundaries (HBM3E != HBM4).
    pattern = r'\s*'.join(re.escape(part) for part in token.split())
    return bool(re.search(r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])', text, re.I))


def _support(topic, question, context, at):
    if not context:
        return [], []
    specific = _technical_tokens(topic, question)
    historical = context.get('temporal_mode') == 'strict_historical'
    citations = context.get('citation_map', {})
    refs = []
    for cid, row in citations.items():
        try:
            usable = row.get('round_trip_verified') is True and parse_time(row['available_at']) <= at
            usable = usable and (not historical or row.get('historical_eligible') is True)
        except (ValueError, TypeError, KeyError):
            usable = False
        text = row.get('text', '')
        if usable and _matches(topic, text) and (not specific or any(_contains_token(text, s) for s in specific)):
            refs.append(cid)
    refs = sorted(refs)
    relations = []
    entities = context.get('entity_map', {})
    for rid, row in context.get('assertion_map', {}).items():
        citation_ids = row.get('citation_ids', [])
        hay = ' '.join(str(row.get(k, '')) for k in ('claim','business_scope','subject_id','object_id'))
        hay += ' ' + ' '.join(entities.get(row.get(k), {}).get('name', '') for k in ('subject_id','object_id'))
        if citation_ids and set(citation_ids) <= set(refs) and _matches(topic, hay):
            relations.append(rid)
    return refs, sorted(relations)


def _query_anchor(topic, question, refs, context):
    specific = _technical_tokens(topic, question)
    if specific:
        return ' '.join(specific[:2])
    if not refs:
        return ''
    text = ' '.join(context['citation_map'][ref]['text'] for ref in refs)
    patterns = {
        'memory_supply':r'\bHBM[234](?:E)?\b',
        'advanced_packaging':r'CoWoS(?:\s*-\s*[SRL])?|\bABF\b',
        'foundry_ramp':r'\bN[2345][A-Z]?\b|\b[2345]\s*nm\b',
        'storage_supply_demand':r'SOCAMM\d+|\b\d{4}\s*SSD\b',
    }
    values = list(dict.fromkeys(m.group() for m in re.finditer(patterns.get(topic['key'], r'(?!)'), text, re.I | re.ASCII)))
    return ' '.join(values[:2])


def _normalize_query(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def plan_search_tasks(ticker, question, as_of, industry_context=None, *, max_queries=2, lookback_days=60):
    """Plan at most two industry-change searches; no external retrieval occurs.

    A has only explicit question vocabulary and therefore search hypotheses. B
    may add exact evidence-visible products, source references and typed paths.
    A query hash describes the reusable search itself and never contains ticker.
    """
    identity = resolve_company(ticker); symbol = identity['research_symbol']; _question(question)
    if type(max_queries) is not int or max_queries < 0:
        raise ValueError('max_queries must be a nonnegative integer')
    if type(lookback_days) is not int or not 1 <= lookback_days <= 3650:
        raise ValueError('lookback_days must be 1..3650')
    context = industry_context
    at, _ = freeze(as_of)
    if context:
        if context.get('research_symbol', context.get('ticker')) != symbol:
            raise ValueError('IndustryContext belongs to a different company')
        frozen = parse_time(context['analysis_time'])
        if as_of == 'current':
            if frozen > at:
                raise ValueError('IndustryContext analysis time is in the future')
            at = frozen
        elif frozen != at:
            raise ValueError('IndustryContext and SearchTask must use the same analysis time')
    if not max_queries:
        return []
    ranked = []
    for position, topic in enumerate(TOPICS):
        hits = _matches(topic, question)
        refs, relations = _support(topic, question, context, at)
        if hits:
            ranked.append(((0, hits[0].start(), position), topic, refs, relations))
        elif refs:
            # B can discover an evidence-visible adjacent topic; it cannot flood
            # the graph or introduce every catalog entity into queries.
            ranked.append(((1, -len(relations), position), topic, refs, relations))
    ranked.sort(key=lambda item:item[0])
    tasks = []
    window = {'start':(at-timedelta(days=lookback_days)).isoformat(), 'end':at.isoformat(), 'lookback_days':lookback_days}
    for _, topic, refs, relations in ranked[:min(max_queries,2)]:
        anchor = _query_anchor(topic, question, refs, context)
        query_en = ' '.join(filter(None, (anchor, topic['base'], topic['en'])))
        query_zh = ' '.join(filter(None, (anchor, topic['zh'], topic['changes_zh'])))
        regions = ['global']
        preferences = ['official_company_disclosures', 'official_regulator_or_grid_operator',
                       'industry_reporting_with_full_article_body']
        query_hash = _digest({'query_en':_normalize_query(query_en), 'query_zh':_normalize_query(query_zh),
                              'time_window':window, 'regions':regions})
        conditions = list(topic['conditions'])
        if context:
            conditions += list(dict.fromkeys(context['assertion_map'][rid].get('conditions', '')
                for rid in relations if context['assertion_map'][rid].get('conditions')))
        entities = sorted({context['assertion_map'][rid][key] for rid in relations
                           for key in ('subject_id','object_id')}) if context else []
        background_unknowns = context.get('unknowns', []) if context and refs else []
        tasks.append(dict(task_id='ST_'+_digest([symbol, topic['key'], query_hash])[:28],
            topic=topic['label'], topic_key=topic['key'], change=topic['change'],
            query_en=query_en, query_zh=query_zh, query_hash=query_hash,
            background_evidence_ids=refs,
            potential_impact_path=dict(status='conditional_search_direction', conditional=True,
                steps=topic['mechanism'], target_company=symbol,
                background_relation_ids=relations, background_entity_ids=entities,
                background_path_ids=[path['path_id'] for path in context.get('paths', [])
                    if path.get('steps') and all(step.get('relation_id') in relations for step in path['steps'])] if context else [],
                procurement_implied=False, causal_effect_estimated=False),
            conditions=list(dict.fromkeys(conditions)),
            unknowns=list(dict.fromkeys(topic['unknowns'] + background_unknowns)),
            time_window=deepcopy(window), regions=regions, source_preferences=preferences,
            query_basis=dict(classification='evidence_derived_search_direction' if refs else 'search_hypothesis',
                question_terms=list(dict.fromkeys(m.group() for m in _matches(topic, question))),
                exact_product_terms=anchor.split() if anchor else [],
                background_context_id=context.get('context_id') if context and refs else None,
                supporting_citation_ids=refs, supporting_relation_ids=relations,
                explanation='Evidence-visible industry/product terms define a question to verify; no new fact is asserted.'
                    if refs else 'Only explicit industry terms in the question define this unverified search direction.',
                planner_version=PLANNER_VERSION)))
    return tasks
