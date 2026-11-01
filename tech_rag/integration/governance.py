"""JSON-only research governance. No model, database, trading or permission APIs.

Trusted boundary: packets must come from the local, read-only RAG verifier, not
from an LLM. Its coordinate/hash verification flags are attestations, not digital
signatures. Exact quotation and curated scopes are checked deterministically;
arbitrary paraphrase, causation, truth and economic models still need review.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

CLAIM_TYPES = {'original_fact', 'news_event', 'research_interpretation',
               'conditional_hypothesis', 'stock_return_hypothesis'}
REQUIRED = {'claim_id', 'text', 'type', 'subject', 'product', 'business_scope',
            'evidence_ids', 'parent_claim_ids', 'conditions', 'unknowns',
            'available_at', 'valid_time', 'analysis_time', 'impact_target', 'horizon', 'status'}
LIMITS = [
    'Deterministic exact-quote/curated-scope and inheritance checks do not prove factual truth or semantic entailment.',
    'Accepted hypotheses are conditional research proposals, never reviewed facts or estimated causal returns.',
    'Packet verification flags and original-event identity rely on a trusted local tool boundary; external text cannot grant authority.',
    'Registry hashes detect accidental edits inside a run; they are not an authenticated external audit log.',
    'Event identity is explicit-source deduplication; unspecified cross-source event equivalence needs human review.',
]


def _copy(value):
    # Round trip both rejects non-JSON inputs and detaches callers' references.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('timezone-aware timestamp required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    return stamp.astimezone(timezone.utc)


def _strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def _issue(code, detail='', severity='error', **refs):
    return dict(code=code, detail=detail, severity=severity, **refs)


def _record_issues(shared, issues):
    for issue in issues:
        if issue not in shared['review_issues']:
            shared['review_issues'].append(_copy(issue))


def _seal(record):
    item = _copy(record)
    item['record_hash'] = _hash(item)
    return item


def _intact(record):
    if not isinstance(record, dict):
        return False
    return record.get('record_hash') == _hash({k: v for k, v in record.items() if k != 'record_hash'})


def new_shared_state(analysis_time, historical):
    """Freeze the run's clock; historical/current changes require a fresh state."""
    _time(analysis_time)
    if not isinstance(historical, bool):
        raise ValueError('historical must be boolean')
    return dict(schema_version='research_governance_v3', analysis_time=analysis_time,
                historical=historical, permissions=['research_read_only'],
                evidence_registry={}, evidence_observations={}, assertion_registry={}, event_registry={}, claim_ledger={},
                unresolved_gaps=[], review_issues=[], strategy_hypotheses=[],
                research_gate={'status': 'needs_more_evidence', 'execution_allowed': False,
                               'independent_event_count': 0}, semantic_validation_limits=list(LIMITS))


def _eligible(shared, record):
    try:
        if _time(record['available_at']) > _time(shared['analysis_time']):
            return 'evidence_not_yet_available'
        if shared['historical'] and record.get('historical_eligible') is not True:
            return 'historical_version_unverified'
        return None
    except (ValueError, TypeError, KeyError):
        return 'invalid_available_at'


def _add_registry(shared, registry, key, value, issues):
    old = shared[registry].get(key)
    sealed = _seal(value)
    if old and old != sealed:
        issues.append(_issue('registry_id_collision', registry=registry, record_id=key))
        return False
    shared[registry][key] = sealed
    return True


def _observe_evidence(shared, eid, original_record, unknowns):
    observation = dict(evidence_id=eid, original_record=_copy(original_record), packet_unknowns=list(unknowns))
    observation['observation_id'] = 'OBS_' + _hash(observation)[:32]
    observation = _seal(observation)
    observations = shared.setdefault('evidence_observations', {}).setdefault(eid, [])
    if observation not in observations:
        observations.append(observation)


def _covered_quote(locator, citations):
    """Exact original-C coverage only; no EV-to-EV or cross-version assembly."""
    if not citations or any(c.get('registry_kind') != 'exact_original_quote' or not _intact(c) for c in citations):
        raise ValueError('coverage_requires_verified_original_chunks')
    if any(c.get('source_id') != locator.get('source_id')
           or c.get('source_sha256') != locator.get('source_sha256') for c in citations):
        raise ValueError('reviewed_evidence_version_mismatch')
    loc = _copy(locator)
    if loc.get('char_start') is None and loc.get('char_end') is None:
        # Legacy whole-container coverage still has a precise locator derived
        # from the verified originals; gaps between containers remain invalid.
        loc.update(char_start=min(c['char_start'] for c in citations),
                   char_end=max(c['char_end'] for c in citations))
        for key in ('pdf_page', 'paragraph'):
            if key not in loc:
                loc[key] = citations[0].get(key)
    if (type(loc.get('char_start')) is not int or type(loc.get('char_end')) is not int
            or not 0 <= loc['char_start'] < loc['char_end']):
        raise ValueError('incomplete_reviewed_evidence')
    position, pieces = loc['char_start'], []
    for c in sorted(citations, key=lambda row: row['char_start']):
        if (type(c.get('char_start')) is not int or type(c.get('char_end')) is not int
                or c['char_end'] - c['char_start'] != len(c.get('text', ''))):
            raise ValueError('invalid_quote_coordinates')
        if c.get('pdf_page') != loc.get('pdf_page') or c.get('paragraph') != loc.get('paragraph'):
            raise ValueError('reviewed_evidence_locator_mismatch')
        lo, hi = max(position, c['char_start']), min(loc['char_end'], c['char_end'])
        if hi <= lo:
            continue
        if c['char_start'] > position:
            raise ValueError('incomplete_reviewed_evidence')
        pieces.append(c['text'][lo-c['char_start']:hi-c['char_start']])
        position = hi
    if position != loc['char_end']:
        raise ValueError('incomplete_reviewed_evidence')
    return ''.join(pieces), loc


def _evidence_unknowns(shared, evidence_id, visiting=None):
    """Current unknowns across immutable EV observations and their original Cs."""
    visiting = set() if visiting is None else visiting
    if evidence_id in visiting or len(visiting) >= 64:
        raise ValueError('cyclic_evidence_lineage')
    evidence = shared['evidence_registry'].get(evidence_id)
    if not evidence or not _intact(evidence) or _eligible(shared, evidence):
        raise ValueError('unqualified_evidence_lineage')
    observations = shared.get('evidence_observations', {}).get(evidence_id, [])
    if not observations:
        raise ValueError('missing_evidence_observation')
    unknowns = set(evidence.get('packet_unknowns', []))
    visiting.add(evidence_id)
    try:
        for observation in observations:
            if (not _intact(observation) or observation.get('evidence_id') != evidence_id
                    or not _strings(observation.get('packet_unknowns'))):
                raise ValueError('invalid_evidence_observation')
            original = observation.get('original_record', {})
            for key in ('source_id', 'source_sha256', 'pdf_page', 'paragraph', 'char_start', 'char_end'):
                if original.get(key) != evidence.get(key):
                    raise ValueError('evidence_observation_version_mismatch')
            unknowns.update(observation['packet_unknowns'])
            if evidence.get('registry_kind') == 'exact_original_quote':
                if original.get('text') != evidence.get('text'):
                    raise ValueError('evidence_observation_text_mismatch')
            elif evidence.get('registry_kind') == 'reviewed_exact_evidence':
                refs = original.get('citation_ids')
                if (not _strings(refs) or not refs or original.get('fully_covered') is not True
                        or original.get('quote_round_trip_verified') is not True):
                    raise ValueError('incomplete_reviewed_evidence')
                citations = [shared['evidence_registry'].get(ref, {}) for ref in refs]
                quote, _ = _covered_quote(original, citations)
                if quote != evidence.get('text'):
                    raise ValueError('evidence_observation_text_mismatch')
                for ref in refs:
                    unknowns.update(_evidence_unknowns(shared, ref, visiting))
            else:
                raise ValueError('unknown_evidence_registry_kind')
        return unknowns
    finally:
        visiting.remove(evidence_id)


def register_packet(shared, packet):
    """Register verified RAG citations and reviewed assertions, never graph paths.

    Evidence IDs may be either citation IDs or fully covered curated evidence
    IDs. Reuse of an ID with different content fails closed; versions need new
    IDs, while event and claim registries retain explicit version chains.
    """
    issues, accepted = [], []
    try:
        p = _copy(packet)
        if not isinstance(p, dict) or not isinstance(p.get('citations'), dict):
            raise ValueError('packet and citations must be mappings')
        if _time(p['analysis_time']) != _time(shared['analysis_time']):
            raise ValueError('packet clock differs from frozen run clock')
        if shared['historical'] and p.get('temporal_mode') != 'strict_historical':
            raise ValueError('historical packet required')
        if not isinstance(p.get('evidence_map', {}), dict) or not isinstance(p.get('assertion_map', {}), dict):
            raise ValueError('evidence/assertion maps must be mappings')
        if not _strings(p.get('unknowns', [])) or not _strings(p.get('insufficiency_reasons', [])):
            raise ValueError('packet gaps must be string lists')
    except (ValueError, TypeError, KeyError, OverflowError):
        issues.append(_issue('invalid_packet', 'Malformed packet or clock/mode mismatch.'))
        _record_issues(shared, issues)
        return {'accepted_evidence_ids': [], 'issues': issues}
    for gap in p.get('unknowns', []) + p.get('insufficiency_reasons', []):
        if gap not in shared['unresolved_gaps']:
            shared['unresolved_gaps'].append(gap)
    for key, c in p['citations'].items():
        reason = None
        if not isinstance(key, str) or not isinstance(c, dict):
            reason = 'invalid_citation'
        elif c.get('citation_id') != key or not isinstance(c.get('text'), str) or not c['text'].strip():
            reason = 'invalid_citation'
        elif not isinstance(c.get('source_id'), str) or not re.fullmatch(r'[0-9a-fA-F]{64}', str(c.get('source_sha256', ''))):
            reason = 'unverified_source_version'
        elif c.get('round_trip_verified') is not True:
            reason = 'unverified_exact_quote'
        elif (type(c.get('char_start')) is not int or type(c.get('char_end')) is not int
              or c['char_start'] < 0 or c['char_end'] - c['char_start'] != len(c['text'])):
            reason = 'invalid_quote_coordinates'
        else:
            reason = _eligible(shared, c)
        if reason:
            issues.append(_issue(reason, evidence_id=str(key)))
            continue
        original_c = _copy(c)
        c['registry_kind'] = 'exact_original_quote'
        for context_key in ['retrieval_role', 'overlapping_reviewed_evidence_ids', 'packet_unknowns', 'citation_ids']:
            c.pop(context_key, None)
        if _add_registry(shared, 'evidence_registry', key, c, issues):
            accepted.append(key)
            _observe_evidence(shared, key, original_c, p.get('unknowns', []))
    for eid, e in p.get('evidence_map', {}).items():
        if not isinstance(eid, str) or not isinstance(e, dict):
            issues.append(_issue('invalid_reviewed_evidence'))
            continue
        refs = e.get('citation_ids')
        if e.get('fully_covered') is not True or e.get('quote_round_trip_verified') is not True or not _strings(refs) or not refs:
            issues.append(_issue('incomplete_reviewed_evidence', evidence_id=eid))
            continue
        cs = [shared['evidence_registry'].get(ref) for ref in refs]
        if not all(c and c.get('source_id') == e.get('source_id') and c.get('source_sha256') == e.get('source_sha256') for c in cs):
            issues.append(_issue('reviewed_evidence_version_mismatch', evidence_id=eid))
            continue
        try:
            quote, e = _covered_quote(e, cs)
            inherited_unknowns = set(p.get('unknowns', []))
            for ref in refs:
                inherited_unknowns.update(_evidence_unknowns(shared, ref))
        except (KeyError, TypeError, ValueError, OverflowError):
            issues.append(_issue('incomplete_or_invalid_evidence_lineage', evidence_id=eid))
            continue
        value = {**e, 'text': quote, 'registry_kind': 'reviewed_exact_evidence',
                 'available_at': max((c['available_at'] for c in cs), key=_time),
                 'historical_eligible': all(c.get('historical_eligible') is True for c in cs)}
        for context_key in ['citation_ids', 'packet_unknowns', 'retrieval_role', 'overlapping_reviewed_evidence_ids']:
            value.pop(context_key, None)
        if _add_registry(shared, 'evidence_registry', eid, value, issues):
            accepted.append(eid)
            _observe_evidence(shared, eid, e, sorted(inherited_unknowns))
    for rid, a in p.get('assertion_map', {}).items():
        if not isinstance(rid, str) or not isinstance(a, dict):
            issues.append(_issue('invalid_assertion'))
            continue
        eids = a.get('evidence_ids')
        if (not _strings(eids) or not eids or not set(eids) <= set(shared['evidence_registry'])
                or a.get('review_status') != 'curated_with_matched_evidence'
                or a.get('fact_edge_eligible') is not True or a.get('evidence_class') != 'original_disclosure'
                or _eligible(shared, a)):
            issues.append(_issue('unqualified_reviewed_assertion', assertion_id=rid))
            continue
        # The reviewed relation identity is independent of which page excerpts
        # happened to cover its same exact EV evidence spans in this retrieval.
        a.pop('citation_ids', None)
        _add_registry(shared, 'assertion_registry', rid, a, issues)
    _record_issues(shared, issues)
    return {'accepted_evidence_ids': accepted, 'issues': _copy(issues)}


def _malformed_claim(claim):
    if not isinstance(claim, dict) or not REQUIRED <= set(claim):
        return True
    if any(not isinstance(claim[k], str) or not claim[k].strip() for k in
           ['claim_id', 'text', 'type', 'subject', 'product', 'business_scope', 'impact_target', 'horizon', 'status']):
        return True
    if claim['type'] not in CLAIM_TYPES or any(not _strings(claim[k]) for k in
            ['evidence_ids', 'parent_claim_ids', 'conditions', 'unknowns']):
        return True
    valid = claim['valid_time']
    if not isinstance(valid, dict) or not {'from', 'to'} <= set(valid):
        return True
    try:
        _time(claim['available_at'])
        _time(claim['analysis_time'])
        bounds = []
        for k in ['from', 'to']:
            v = valid[k]
            if v is not None:
                if not isinstance(v, str):
                    return True
                bounds.append(datetime.fromisoformat(v.replace('Z', '+00:00')).date())
        return len(bounds) == 2 and bounds[0] > bounds[1]
    except (ValueError, TypeError):
        return True


def _mentioned(subject, text):
    aliases = {'MU': ['micron', '美光'], 'NVDA': ['nvidia', '英伟达'], 'TSM': ['tsmc', '台积电']}
    return any(re.search(r'(?<![a-z0-9])' + re.escape(x) + r'(?![a-z0-9])', text, re.I)
               for x in [subject] + aliases.get(subject, []))


def _specific_tokens(text):
    return set(x.upper() for x in re.findall(r'HBM\d*[A-Z0-9]*|SOCAMM\d*|CoWoS[-\s]?[SRL]|\d+\s*(?:GB|TB)', text, re.I))


def _complete_original_quote(text, quote):
    """Require a complete container or contiguous complete sentence boundaries.

    This is a lexical boundary check, not a language or entailment model. It
    deliberately refuses fragments rather than detaching negation/conditions
    from their sentence. Separate sentences may still qualify one another.
    """
    if quote == text:
        return True
    if not quote.rstrip().endswith(('.', '!', '?', '。', '！', '？')):
        return False
    for match in re.finditer(re.escape(quote), text):
        prefix = text[:match.start()]
        suffix = text[match.end():]
        # English full stops require a whitespace boundary. Chinese sentence
        # punctuation may directly adjoin the following sentence. This also
        # avoids accepting part of a decimal as a new English sentence.
        if prefix.strip() and not re.search(r'(?:[.!?]\s+|[。！？]\s*)$', prefix):
            continue
        if suffix and quote.rstrip().endswith('.') and not suffix[0].isspace():
            continue
        return True
    return False


def _fact_support(shared, c, evidence):
    """Exact quotation or exact curated claim + scope, never fuzzy entailment."""
    texts = [e.get('text', '') for e in evidence]
    for a in shared['assertion_registry'].values():
        if (_intact(a) and a.get('claim') == c['text'] and a.get('subject_id') == c['subject']
                and a.get('business_scope') == c['business_scope']
                and set(a.get('evidence_ids', [])) <= set(c['evidence_ids'])
                and not _specific_tokens(c['product']) - _specific_tokens(a.get('claim', '') + ' ' + a.get('business_scope', ''))):
            required = [a['conditions']] if a.get('conditions') else []
            if not set(required) <= set(c['conditions']):
                return False, 'dropped_assertion_conditions'
            return True, None
    for text in texts:
        if c['text'] not in text:
            continue
        if not _complete_original_quote(text, c['text']):
            return False, 'incomplete_original_sentence'
        # A substring must not detach an if/unless clause from the same
        # sentence. Exact full-sentence quoting is a deliberately conservative
        # boundary; more complex conditional semantics still require review.
        sentences = re.split(r'(?<=[.!?。！？])\s*', text)
        enclosing = next((part for part in sentences if c['text'] in part), text)
        if (enclosing != c['text'] and re.search(r'\bif\b|\bunless\b|subject to|provided that|如果|除非|取决于|仅在', enclosing, re.I)
                and enclosing not in c['conditions']):
            return False, 'dropped_quote_conditions'
        if not _mentioned(c['subject'], c['text']):
            continue
        if _specific_tokens(c['product'] + ' ' + c['business_scope']) - _specific_tokens(c['text']):
            continue
        # For original quotes a free-form scope must itself be grounded in the
        # quote, or explicitly unspecified. This blocks fabricated broad labels.
        if c['product'].casefold() not in c['text'].casefold() and c['product'].casefold() not in {'unspecified', '未披露'}:
            continue
        if c['business_scope'].casefold() not in c['text'].casefold() and c['business_scope'].casefold() not in {'unspecified', '未披露'}:
            continue
        return True, None
    return False, 'no_exact_quote_or_matching_reviewed_scope'


def claim_usable(shared, claim_id):
    """Whether a latest accepted version may support another role *now*.

    Stored statuses are immutable audit results. A later parent revision or
    evidence observation can invalidate reuse without rewriting that history.
    Recursively recheck current parent versions, evidence, unknowns and hashes.
    Cyclic or malformed persisted metadata fails closed.
    """
    memo, visiting = {}, set()

    def usable(cid):
        if cid in memo:
            return memo[cid]
        if cid in visiting or len(visiting) >= 64:
            return False
        versions = shared['claim_ledger'].get(cid, [])
        if not versions:
            return False
        previous_hash = None
        for index, record in enumerate(versions, 1):
            if (not _intact(record) or record.get('version') != index
                    or record.get('previous_version_hash') != previous_hash):
                return False
            previous_hash = record['record_hash']
        current = versions[-1]
        if (current.get('claim_id') != cid or current.get('status') != 'accepted'
                or _time(current['analysis_time']) != _time(shared['analysis_time'])
                or _time(current['available_at']) > _time(shared['analysis_time'])):
            return False
        unknowns = set(current['unknowns'])
        for eid in current['evidence_ids']:
            if not _evidence_unknowns(shared, eid) <= unknowns:
                return False
        parents = current.get('parent_versions', {})
        if not isinstance(parents, dict) or set(parents) != set(current['parent_claim_ids']):
            return False
        visiting.add(cid)
        try:
            for pid, inspected in parents.items():
                parent_versions = shared['claim_ledger'].get(pid, [])
                if (type(inspected) is not int or len(parent_versions) != inspected
                        or not usable(pid)):
                    return False
            memo[cid] = True
            return True
        finally:
            visiting.remove(cid)

    try:
        return bool(usable(claim_id))
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        return False


def eligible_claims(shared):
    """Detached currently reusable claims; never fall back to old acceptance."""
    return [_copy(versions[-1]) for cid, versions in shared['claim_ledger'].items()
            if versions and claim_usable(shared, cid)]


def append_claim(shared, claim, role):
    """Validate and append a detached immutable version; return claim + issues.

    Rejected claims remain in the audit ledger and cannot support children.
    Malformed inputs get issues only. Updating the same ID appends a version;
    parent_versions records exactly which parent versions were inspected.
    """
    issues = []
    try:
        c = _copy(claim)
        if _malformed_claim(c) or not isinstance(role, str) or not role.strip():
            raise ValueError('invalid claim schema')
    except (ValueError, TypeError, OverflowError):
        issues.append(_issue('invalid_claim_schema'))
        _record_issues(shared, issues)
        return {'claim': None, 'issues': issues}
    cid = c['claim_id']
    def issue(code, severity='error', detail=''):
        issues.append(_issue(code, detail, severity, claim_id=cid))
    if any(k in c for k in ['permissions', 'system_prompt', 'grant_authority', 'execution_allowed']):
        issue('external_authority_request')
    if _time(c['analysis_time']) != _time(shared['analysis_time']):
        issue('claim_clock_mismatch')
    if _time(c['available_at']) > _time(shared['analysis_time']):
        issue('claim_not_yet_available')
    if re.search(r'stock.return|share.price|股价|股票收益', c['impact_target'], re.I) and c['type'] != 'stock_return_hypothesis':
        issue('return_requires_stock_hypothesis_type')
    evidence = []
    for eid in c['evidence_ids']:
        e = shared['evidence_registry'].get(eid)
        if not e or not _intact(e) or _eligible(shared, e):
            issue('missing_or_unqualified_evidence', detail=eid)
        else:
            evidence.append(e)
            if _time(c['available_at']) < _time(e['available_at']):
                issue('claim_predates_evidence', detail=eid)
    if not c['evidence_ids']:
        issue('missing_evidence')
    parent_versions = {}
    parents = []
    for pid in c['parent_claim_ids']:
        versions = shared['claim_ledger'].get(pid)
        if not versions or pid == cid:
            issue('missing_or_cyclic_parent', detail=pid)
            continue
        parent = versions[-1]
        if not claim_usable(shared, pid):
            issue('unqualified_parent', detail=pid)
        parents.append(parent)
        parent_versions[pid] = parent['version']
        for key, code in [('conditions', 'dropped_parent_conditions'), ('unknowns', 'dropped_parent_unknowns')]:
            if not set(parent[key]) <= set(c[key]):
                issue(code, detail=pid)
        if _time(c['available_at']) < _time(parent['available_at']):
            issue('claim_predates_parent', detail=pid)
        if c['type'] in {'original_fact', 'news_event'} and parent['type'] not in {'original_fact', 'news_event'}:
            issue('hypothesis_promoted_to_fact', detail=pid)
        if parent['subject'] != c['subject'] or parent['product'] != c['product'] or parent['business_scope'] != c['business_scope']:
            issue('changed_parent_scope_requires_bridge', 'gap' if c['type'] not in {'original_fact', 'news_event'} else 'error', pid)
        if parent['valid_time'] != c['valid_time'] or parent['horizon'] != c['horizon']:
            issue('changed_parent_time_scope_requires_bridge', 'gap' if c['type'] not in {'original_fact', 'news_event'} else 'error', pid)
    previous = shared['claim_ledger'].get(cid, [])
    if previous:
        for key in ['conditions', 'unknowns']:
            if not set(previous[-1][key]) <= set(c[key]):
                issue('version_drops_' + key)
    if c['type'] in {'original_fact', 'news_event'}:
        supported, why = _fact_support(shared, c, evidence)
        if not supported:
            issue(why)
    else:
        source_tokens = set().union(*(_specific_tokens(e.get('text', '')) for e in evidence)) if evidence else set()
        if _specific_tokens(c['text'] + ' ' + c['product'] + ' ' + c['business_scope']) - source_tokens:
            issue('ungrounded_technical_scope', 'gap', 'A technical product/model is hypothetical rather than documented in cited evidence.')
        if not re.search(r'\bmay\b|\bmight\b|\bcould\b|\bif\b|可能|如果|若|假设|取决于|不确定'
                         r'|\bdoes not(?: by itself)? (?:establish|prove|demonstrate|confirm)\b'
                         r'|\bcannot (?:establish|infer|determine|identify|confirm|quantify)\b', c['text'], re.I):
            issue('missing_uncertainty')
        if not c['conditions']:
            issue('missing_hypothesis_conditions')
        if re.search(r'definitely|guaranteed|certainly|必然|必定|保证|确定上涨|一定上涨|will\s+(?:definitely\s+)?(?:rise|increase|fall)', c['text'], re.I):
            issue('unjustified_certainty')
        for e in evidence:
            if re.search(r'Customer\s*[A-Z]\b|匿名客户|客户[ABC]', e.get('text', ''), re.I):
                if re.search(r'Microsoft|Amazon|Apple|微软|亚马逊|苹果', c['text'], re.I) and not re.search(r'identity.*(?:unknown|undisclosed)|身份.*(?:未知|未披露)', c['text'], re.I):
                    issue('anonymous_customer_named')
        if c['type'] == 'stock_return_hypothesis' or re.search(r'stock.return|share.price|股价|股票收益', c['impact_target'], re.I):
            if c['type'] != 'stock_return_hypothesis':
                issue('return_requires_stock_hypothesis_type')
            bridge = [e for e in evidence if e.get('evidence_role') in {'valuation', 'factor', 'valuation_factor', 'return_model'}]
            roles = {e.get('evidence_role') for e in bridge}
            if not ('valuation_factor' in roles or 'return_model' in roles or {'valuation', 'factor'} <= roles):
                issue('missing_return_bridge', 'gap', 'Additional valuation and factor/model evidence required.')
    # A declaration cannot erase source packet gaps, even without a parent.
    source_unknowns = set()
    for eid in c['evidence_ids']:
        try:
            source_unknowns.update(_evidence_unknowns(shared, eid))
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            issue('unqualified_evidence_lineage', detail=eid)
    if not source_unknowns <= set(c['unknowns']):
        issue('dropped_evidence_unknowns')
    # Check the full existing chain before allowing it to support a new version.
    if (any(not _intact(v) for vs in shared['claim_ledger'].values() for v in vs)
            or any(not _intact(v) for v in shared['assertion_registry'].values())):
        issue('ledger_integrity_violation')
    c['status'] = ('rejected' if any(i['severity'] == 'error' for i in issues) else
                   'needs_more_evidence' if any(i['severity'] == 'gap' for i in issues) else 'accepted')
    c.update(role=role, version=len(previous) + 1, parent_versions=parent_versions,
             previous_version_hash=previous[-1]['record_hash'] if previous else None,
             validation_issues=_copy(issues), semantic_review_required=c['type'] not in {'original_fact', 'news_event'})
    c = _seal(c)
    shared['claim_ledger'].setdefault(cid, []).append(c)
    for i in issues:
        if i['severity'] == 'gap' and i['detail'] not in shared['unresolved_gaps']:
            shared['unresolved_gaps'].append(i['detail'] or i['code'])
    if c['type'] in {'conditional_hypothesis', 'stock_return_hypothesis'} and c['status'] != 'rejected':
        shared['strategy_hypotheses'].append(dict(claim_id=cid, version=c['version'], status=c['status'],
                                                  claim_hash=c['record_hash']))
    _record_issues(shared, issues)
    review_ledger(shared)
    return {'claim': _copy(c), 'issues': _copy(issues)}


def review_ledger(shared):
    """Review all chains and derive a research-only gate without confidence sums."""
    issues = []
    for registry in ['evidence_registry', 'assertion_registry']:
        for rid, record in shared[registry].items():
            if not _intact(record):
                issues.append(_issue('registry_integrity_violation', registry=registry, record_id=rid))
    for eid, observations in shared.get('evidence_observations', {}).items():
        if any(not _intact(observation) for observation in observations):
            issues.append(_issue('registry_integrity_violation', registry='evidence_observations', record_id=eid))
    for registry in ['claim_ledger', 'event_registry']:
        for rid, versions in shared[registry].items():
            previous_hash = None
            for index, record in enumerate(versions, 1):
                if not _intact(record) or record.get('version') != index or record.get('previous_version_hash') != previous_hash:
                    issues.append(_issue('ledger_integrity_violation', registry=registry, record_id=rid))
                previous_hash = record.get('record_hash')
    # Retain the old version unchanged, but make downstream use of a superseded
    # parent explicit. A new child version must inspect the revised parent.
    for cid, versions in shared['claim_ledger'].items():
        if not versions:
            continue
        current = versions[-1]
        observed_unknowns = set()
        for eid in current.get('evidence_ids', []):
            try:
                observed_unknowns.update(_evidence_unknowns(shared, eid))
            except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
                issues.append(_issue('unqualified_evidence_lineage', 'Original citation lineage requires review.',
                                     claim_id=cid, evidence_id=eid))
        if not observed_unknowns <= set(current.get('unknowns', [])):
            issues.append(_issue('new_evidence_unknowns_require_review',
                'A later evidence observation introduced unknowns absent from this immutable claim version.',
                'gap', claim_id=cid))
        for pid, version in versions[-1].get('parent_versions', {}).items():
            parents = shared['claim_ledger'].get(pid, [])
            if not parents or len(parents) != version:
                issues.append(_issue('stale_parent_version', 'Parent revision requires a new child review.',
                                     'gap', claim_id=cid, parent_claim_id=pid, inspected_version=version))
    events = [v[-1] for v in shared['event_registry'].values() if v and v[-1].get('status') == 'registered']
    for index, left in enumerate(events):
        for right in events[index+1:]:
            channels = set(left.get('information_channels', [])) | set(right.get('information_channels', []))
            if (left.get('subject') == right.get('subject') and channels & {'price', 'momentum'}
                    and (len(channels & {'price', 'momentum'}) == 2 or set(left.get('evidence_ids', [])) & set(right.get('evidence_ids', [])))):
                issues.append(_issue('possible_information_dependence',
                    'Price/momentum may reflect overlapping information; no additive confidence.', 'warning',
                    event_ids=[left['event_id'], right['event_id']]))
    _record_issues(shared, issues)
    claims = [v[-1] for v in shared['claim_ledger'].values() if v]
    integrity_bad = any(i['code'] in {'registry_integrity_violation', 'ledger_integrity_violation', 'unqualified_evidence_lineage'} for i in shared['review_issues'])
    status = ('blocked' if integrity_bad or any(c['status'] == 'rejected' for c in claims) else
              'needs_more_evidence' if not claims or any(c['status'] == 'needs_more_evidence' for c in claims)
                or any(i['severity'] == 'gap' for i in issues) else
              'research_ready_with_limits')
    shared['research_gate'] = dict(status=status, execution_allowed=False,
        independent_event_count=len(events), accepted_claim_count=sum(c['status'] == 'accepted' for c in claims),
        rejected_claim_count=sum(c['status'] == 'rejected' for c in claims),
        unresolved_gap_count=len(shared['unresolved_gaps']), review_required=True,
        semantic_validation_limits=list(LIMITS))
    return _copy(dict(issues=shared['review_issues'], research_gate=shared['research_gate'],
                      unresolved_gaps=shared['unresolved_gaps']))


def register_event(shared, event):
    from .event_registry import register_event as register
    return register(shared, event)
