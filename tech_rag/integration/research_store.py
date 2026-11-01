"""Add immutable research audit records to the existing Finance graph.

prepare_research_plan is pure and requires no Neo4j dependencies. The explicit
persist function alone reads tech_rag/.env and opens the already selected DB.
Nothing in this module changes facts, reviewed statuses, services or schemas.
"""
from pathlib import Path
import hashlib
import json
import re

from .governance import _copy, _intact, _time, review_ledger

DATASET = 'tech_company_v1'
NAMESPACE = 'research_v3'
ROOT = Path(__file__).resolve().parents[1]
LABELS = ('TechResearchRun', 'TechResearchClaimVersion',
          'TechResearchEventVersion', 'TechResearchToolInvocation')
EDGE_KINDS = ('TECH_RESEARCH_HAS_CLAIM', 'TECH_RESEARCH_HAS_EVENT',
              'TECH_RESEARCH_USED_TOOL', 'TECH_RESEARCH_PARENT_CLAIM',
              'TECH_RESEARCH_PREVIOUS_VERSION')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _no_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r'^(?:api[_-]?key|password|passwd|private[_-]?key|access[_-]?token|authorization|secret)$', key, re.I):
                raise ValueError('Credential-bearing fields must not enter research audit payloads.')
            _no_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _no_credentials(child)
    elif isinstance(value, str) and re.search(r'\bsk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', value):
        raise ValueError('Credential-like text must not enter research audit payloads.')


def _audit_id(run_id, label, logical_id):
    return 'TRA_' + _digest([NAMESPACE, run_id, label, logical_id])[:40]


def assert_existing_compatible(expected, actual):
    """An existing audit identity is immutable, including its full payload."""
    if actual is not None and _canonical(actual) != _canonical(expected):
        raise ValueError('Research audit identity already has different content; no overwrite permitted.')
    return True


def prepare_research_plan(shared):
    """Return JSON-safe nodes/edges; reject invalid/tampered/secret-bearing runs."""
    try:
        state = _copy(shared)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Research run must contain finite JSON values.') from None
    if not isinstance(state, dict) or not isinstance(state.get('run_id'), str) or not state['run_id'].strip():
        raise ValueError('A nonempty immutable run_id is required.')
    _time(state.get('analysis_time'))
    if state.get('permissions') != ['research_read_only'] or not isinstance(state.get('historical'), bool):
        raise ValueError('Only research-only governance states may be persisted.')
    for key in ['evidence_registry', 'assertion_registry', 'claim_ledger', 'event_registry']:
        if not isinstance(state.get(key), dict):
            raise ValueError('Missing governance registries.')
    for key in ['review_issues', 'unresolved_gaps', 'strategy_hypotheses']:
        if not isinstance(state.get(key), list):
            raise ValueError('Missing governance audit lists.')
    for key in ['tool_trace', 'raw_role_outputs']:
        default = [] if key == 'tool_trace' else {}
        if not isinstance(state.get(key, default), (list, dict)) or (key == 'tool_trace' and not isinstance(state.get(key, []), list)):
            raise ValueError('Invalid research trace or raw-role-output schema.')
    if not isinstance(state.get('framework_metadata', {}), dict):
        raise ValueError('framework_metadata must be a mapping.')
    _no_credentials(state)
    for registry in ['claim_ledger', 'event_registry']:
        if any(not isinstance(vs, list) or not vs for vs in state[registry].values()):
            raise ValueError('Version registries require nonempty lists.')
        if any(not isinstance(record, dict) or type(record.get('version')) is not int
               for versions in state[registry].values() for record in versions):
            raise ValueError('Invalid version-record schema.')
    checked = review_ledger(_copy(state))
    if any(i['code'] in {'registry_integrity_violation', 'ledger_integrity_violation'} for i in checked['issues']):
        raise ValueError('Research ledger integrity verification failed.')
    run_id = state['run_id']
    content_hash = _digest(state)
    nodes = {label: [] for label in LABELS}
    edges = {kind: [] for kind in EDGE_KINDS}
    def node(label, logical_id, payload, **meta):
        row = dict(audit_id=_audit_id(run_id, label, logical_id), dataset_id=DATASET,
                   audit_namespace=NAMESPACE, run_id=run_id, payload=_canonical(payload),
                   content_hash=_digest(payload), **meta)
        nodes[label].append(row)
        return row['audit_id']
    def edge(kind, start_label, start_id, end_label, end_id):
        identity = [kind, start_label, start_id, end_label, end_id]
        row = dict(audit_id='TRAE_' + _digest(identity)[:40], dataset_id=DATASET,
                   audit_namespace=NAMESPACE, run_id=run_id, start_label=start_label,
                   start_id=start_id, end_label=end_label, end_id=end_id)
        if row not in edges[kind]:
            edges[kind].append(row)
    run_aid = node('TechResearchRun', run_id, state, analysis_time=state['analysis_time'],
                   historical=state['historical'], fact_promotion_allowed=False)
    claim_ids = {}
    for cid, versions in sorted(state['claim_ledger'].items()):
        for version in versions:
            if version.get('claim_id') != cid or not _intact(version):
                raise ValueError('Claim identity or hash mismatch.')
            key = (cid, version['version'])
            aid = node('TechResearchClaimVersion', list(key), version,
                       claim_id=cid, version=version['version'], claim_type=version['type'],
                       research_status=version['status'], fact_promotion_allowed=False)
            claim_ids[key] = aid
            edge('TECH_RESEARCH_HAS_CLAIM', 'TechResearchRun', run_aid, 'TechResearchClaimVersion', aid)
    for cid, versions in sorted(state['claim_ledger'].items()):
        previous = None
        for version in versions:
            aid = claim_ids[(cid, version['version'])]
            if previous:
                edge('TECH_RESEARCH_PREVIOUS_VERSION', 'TechResearchClaimVersion', aid, 'TechResearchClaimVersion', previous)
            previous = aid
            for pid, pversion in version.get('parent_versions', {}).items():
                if (pid, pversion) not in claim_ids:
                    raise ValueError('Missing exact inspected parent version.')
                edge('TECH_RESEARCH_PARENT_CLAIM', 'TechResearchClaimVersion', aid,
                     'TechResearchClaimVersion', claim_ids[(pid, pversion)])
    for eid, versions in sorted(state['event_registry'].items()):
        previous = None
        for version in versions:
            if version.get('event_id') != eid:
                raise ValueError('Event identity mismatch.')
            aid = node('TechResearchEventVersion', [eid, version['version']], version,
                       event_id=eid, version=version['version'],
                       original_source_id=version['original_source_id'],
                       original_event_id=version['original_event_id'])
            edge('TECH_RESEARCH_HAS_EVENT', 'TechResearchRun', run_aid, 'TechResearchEventVersion', aid)
            if previous:
                edge('TECH_RESEARCH_PREVIOUS_VERSION', 'TechResearchEventVersion', aid, 'TechResearchEventVersion', previous)
            previous = aid
    for index, invocation in enumerate(state.get('tool_trace', []), 1):
        if not isinstance(invocation, dict):
            raise ValueError('Each tool invocation must be a JSON object.')
        aid = node('TechResearchToolInvocation', index, invocation, invocation_index=index)
        edge('TECH_RESEARCH_USED_TOOL', 'TechResearchRun', run_aid, 'TechResearchToolInvocation', aid)
    return dict(schema_version='tech_research_write_plan_v3', dataset_id=DATASET,
                audit_namespace=NAMESPACE, run_id=run_id, content_hash=content_hash,
                nodes=nodes, edges=edges, fact_promotion_allowed=False)


def _query_existing(tx, label, row):
    matches = list(tx.run(f'MATCH (n:{label} {{dataset_id:$dataset,audit_namespace:$namespace,audit_id:$id}}) '
                         'RETURN properties(n) AS props', dataset=DATASET, namespace=NAMESPACE, id=row['audit_id']))
    if len(matches) > 1:
        raise ValueError('Duplicate existing research audit identity.')
    assert_existing_compatible(row, matches[0]['props'] if matches else None)


def _merge_plan(tx, plan):
    created = dict(nodes_created=0, relationships_created=0)
    # Check every identity before any writes. A conflict aborts the transaction.
    for label, rows in plan['nodes'].items():
        for row in rows:
            _query_existing(tx, label, row)
    for kind, rows in plan['edges'].items():
        for row in rows:
            found = list(tx.run(f'MATCH (a)-[r:{kind} {{dataset_id:$dataset,audit_namespace:$namespace,audit_id:$id}}]->(b) '
                'RETURN properties(r) AS props,a.audit_id AS start,b.audit_id AS end',
                dataset=DATASET, namespace=NAMESPACE, id=row['audit_id']))
            if len(found) > 1:
                raise ValueError('Duplicate research relationship identity.')
            if found:
                assert_existing_compatible(row, found[0]['props'])
                if found[0]['start'] != row['start_id'] or found[0]['end'] != row['end_id']:
                    raise ValueError('Existing audit relationship endpoints differ.')
    for label, rows in plan['nodes'].items():
        if rows:
            result = tx.run(f'UNWIND $rows AS row MERGE (n:{label} '
                '{dataset_id:$dataset,audit_namespace:$namespace,audit_id:row.audit_id}) '
                'ON CREATE SET n += row', rows=rows, dataset=DATASET, namespace=NAMESPACE).consume()
            created['nodes_created'] += result.counters.nodes_created
    for kind, rows in plan['edges'].items():
        # Endpoint labels are a closed local allowlist, never model-supplied.
        for row in rows:
            if row['start_label'] not in LABELS or row['end_label'] not in LABELS or kind not in EDGE_KINDS:
                raise ValueError('Non-audit schema rejected.')
            result = tx.run(f'MATCH (a:{row["start_label"]} '
                '{dataset_id:$dataset,audit_namespace:$namespace,audit_id:$start}),'
                f'(b:{row["end_label"]} '
                '{dataset_id:$dataset,audit_namespace:$namespace,audit_id:$end}) '
                f'MERGE (a)-[r:{kind} '
                '{dataset_id:$dataset,audit_namespace:$namespace,audit_id:$id}]->(b) '
                'ON CREATE SET r += $row', dataset=DATASET, namespace=NAMESPACE,
                start=row['start_id'], end=row['end_id'], id=row['audit_id'], row=row).consume()
            created['relationships_created'] += result.counters.relationships_created
    return created


def _persist_transaction(tx, plan):
    from neo4j_links import _snapshot, _preserved, _summary
    before = _snapshot(tx)
    first = _merge_plan(tx, plan)
    mid = _snapshot(tx)
    preserved = _preserved(before, mid)
    second = _merge_plan(tx, plan)  # Re-reads and compares every stored payload.
    after = _snapshot(tx)
    _preserved(mid, after)
    if second != {'nodes_created': 0, 'relationships_created': 0} or mid['nodes'] != after['nodes'] or mid['relationships'] != after['relationships']:
        raise RuntimeError('Research audit merge did not prove idempotent; transaction rolled back.')
    return dict(first_run=first, second_run=second, second_run_idempotent=True,
                before=_summary(before), after=_summary(after), **preserved)


def persist_research_run(shared, config=None):
    """Explicit database mutation entry point; no implicit calls or file writes.

    Same run_id with any changed content is refused. Persist a revised result
    under a new run_id. The complete run payload preserves evidence and role
    outputs without labeling model conclusions as reviewed TechAssertions.
    """
    plan = prepare_research_plan(shared)
    expected_config = (ROOT / '.env').resolve()
    if config is not None and Path(config).resolve() != expected_config:
        raise ValueError('Only tech_rag/.env may supply Finance credentials.')
    from neo4j_store import connect, probe
    from neo4j_links import validate_target, _live_identity
    validate_target(probe(expected_config))  # Read only before any write session.
    driver, info = connect(expected_config)
    try:
        with driver.session(database=info['database']) as session:
            target = _live_identity(session, info)  # Revalidate the actual connection.
            result = session.execute_write(_persist_transaction, plan)
    finally:
        driver.close()
    return _copy(dict(schema_version='tech_research_persistence_report_v3', dataset_id=DATASET,
        audit_namespace=NAMESPACE, run_id=plan['run_id'], content_hash=plan['content_hash'],
        target=target, writes_executed=True, old_payloads_rewritten=False,
        fact_review_statuses_changed=False, services_controlled=False,
        credential_values_recorded=False, **result))
