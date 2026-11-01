"""Add exact Tech provenance links to the already selected Finance database.

Only new, namespaced nodes/relationships are created. Existing payloads, labels,
relationships, database configuration and services are never rewritten.
"""
import argparse
from collections import Counter
import json
from pathlib import PureWindowsPath

from common import DATASET, ROOT, dump, load, now, readl, sha
from neo4j_store import connect, probe
from provenance import build_links, location_key

FINANCE_HOME = 'dbms-164b5ace-03b4-45cd-bc0f-ac9c533d6ef4'
LINK_VERSION = 'tech_exact_provenance_v1'
CORE_LABELS = ['TechEntity', 'TechSource', 'TechAssertion', 'TechEvidence',
               'TechChunk', 'TechKeyRecord']
EDGE_SPECS = {
    'TECH_REPRESENTS': ('TechKeyRecord', 'record_id', 'TechAssertion', 'relation_id'),
    'TECH_LOCATED_AT': ('TechEvidence', 'evidence_id', 'TechEvidenceLocator', 'locator_id'),
    'TECH_OVERLAPS_CHUNK': ('TechEvidenceLocator', 'locator_id', 'TechChunk', 'chunk_id'),
    'TECH_EVIDENCE_COVERS': ('TechEvidence', 'evidence_id', 'TechChunk', 'chunk_id'),
    'TECH_IN_SOURCE_VERSION': ('TechChunk', 'chunk_id', 'TechSourceVersion', 'version_id'),
    'TECH_VERSION_OF': ('TechSourceVersion', 'version_id', 'TechSource', 'source_id'),
    'TECH_FROM_SOURCE': ('TechChunk', 'chunk_id', 'TechSource', 'source_id'),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def properties_match(actual, expected):
    # Neo4j omits null properties; a populated coordinate must still match null.
    return all(actual.get(key) == value for key, value in expected.items())


def validate_target(result):
    settings = {r['name']: r['value'] for r in result.get('connection_settings', [])}
    home = settings.get('server.directories.neo4j_home', '')
    if (not result.get('reachable') or result.get('database') != 'neo4j'
            or PureWindowsPath(home).name.lower() != FINANCE_HOME):
        raise RuntimeError('拒绝写入：目标不是已指定的 Desktop Finance 实例及 neo4j 库；不会切换或重启服务')
    return True


def _edge(kind, row):
    start_label, start_key, end_label, end_key = EDGE_SPECS[kind]
    identity = [DATASET, LINK_VERSION, kind, start_label, row[start_key], end_label, row[end_key]]
    for key in ['overlap_start', 'overlap_end']:
        if key in row:
            identity.append(row[key])
    return {**row, 'dataset_id': DATASET, 'link_version': LINK_VERSION,
            'link_id': 'TGL_' + sha(canonical(identity))[:32]}


def prepare_rows(chunks, evidence, records, relations, sources, links=None):
    """Pure validated graph plan: no fuzzy matching and no database access."""
    exact = build_links(chunks, evidence, records, relations)
    if links is not None and canonical(links) != canonical(exact):
        raise ValueError('provenance link plan differs from the exact interval join')
    links = exact
    chunk_map = {c['chunk_id']: c for c in chunks}
    evidence_map = {e['evidence_id']: e for e in evidence}
    source_map = {s['source_id']: s for s in sources}
    record_map = {r['record_id']: r for r in records}
    relation_map = {r['relation_id']: r for r in relations}
    if any(len(rows) != len(mapping) for rows, mapping in [
            (chunks, chunk_map), (evidence, evidence_map), (sources, source_map),
            (records, record_map), (relations, relation_map)]):
        raise ValueError('duplicate graph input identities')
    if any(rid not in relation_map for row in records for rid in row.get('relation_ids', [])):
        raise ValueError('explicit relation card reference points to an absent assertion')
    edges = {name: [] for name in EDGE_SPECS}
    versions = {}
    for c in sorted(chunks, key=lambda x: x['chunk_id']):
        source = source_map.get(c['source_id'])
        if not source or not c.get('source_sha256') or source.get('sha256') != c['source_sha256']:
            raise ValueError('chunk source hash does not match its catalog version')
        version_id = 'TV_' + sha(canonical([c['source_id'], c['source_sha256']]))[:32]
        versions[version_id] = {'version_id': version_id, 'dataset_id': DATASET,
            'source_id': c['source_id'], 'source_sha256': c['source_sha256'],
            'available_at': source.get('available_at'),
            'historical_eligible': source.get('historical_eligible', False),
            'source_url': source.get('url'), 'link_version': LINK_VERSION}
        meta = {key: c.get(key) for key in ['source_id', 'source_sha256', 'available_at',
                'historical_eligible', 'pdf_page', 'paragraph', 'char_start', 'char_end']}
        edges['TECH_IN_SOURCE_VERSION'].append(_edge('TECH_IN_SOURCE_VERSION',
            {**meta, 'chunk_id': c['chunk_id'], 'version_id': version_id}))
        edges['TECH_FROM_SOURCE'].append(_edge('TECH_FROM_SOURCE', {**meta, 'chunk_id': c['chunk_id']}))
    for version in sorted(versions.values(), key=lambda x: x['version_id']):
        edges['TECH_VERSION_OF'].append(_edge('TECH_VERSION_OF', version))
    locators = {}
    for loc in links['locators']:
        e = evidence_map[loc['evidence_id']]
        source = source_map.get(e['source_id'])
        if not source or not e.get('source_sha256') or source.get('sha256') != e['source_sha256']:
            raise ValueError('evidence source hash does not match its catalog version')
        body = {key: value for key, value in loc.items() if key != 'evidence_id'}
        scalar = {key: value for key, value in body.items() if key != 'covered_intervals'}
        locators[loc['locator_id']] = {**scalar, 'dataset_id': DATASET,
            'covered_intervals_json': canonical(loc['covered_intervals']),
            'payload': canonical(body), 'link_version': LINK_VERSION}
        edges['TECH_LOCATED_AT'].append(_edge('TECH_LOCATED_AT',
            {key: e.get(key) for key in ['evidence_id', 'source_id', 'source_sha256',
                'pdf_page', 'paragraph', 'char_start', 'char_end', 'available_at', 'historical_eligible']}
            | {'locator_id': loc['locator_id']}))
    for link in links['evidence_chunk_links']:
        e, c = evidence_map[link['evidence_id']], chunk_map[link['chunk_id']]
        lo, hi = max(e['char_start'], c['char_start']), min(e['char_end'], c['char_end'])
        if location_key(e) != location_key(c) or not lo < hi:
            raise ValueError('invalid exact provenance link')
        row = {**link, **{key: e.get(key) for key in ['source_id', 'source_sha256',
            'pdf_page', 'paragraph', 'available_at', 'historical_eligible']}}
        edges['TECH_OVERLAPS_CHUNK'].append(_edge('TECH_OVERLAPS_CHUNK',
            {key: value for key, value in row.items() if key != 'evidence_id'}))
        edges['TECH_EVIDENCE_COVERS'].append(_edge('TECH_EVIDENCE_COVERS', row))
    for link in links['record_assertion_links']:
        r = relation_map[link['relation_id']]
        edges['TECH_REPRESENTS'].append(_edge('TECH_REPRESENTS', {**link,
            'available_at': r.get('available_at'), 'historical_eligible': r.get('historical_eligible', False),
            'evidence_class': r.get('evidence_class'), 'business_scope': r.get('business_scope')}))
    for kind, rows in edges.items():
        unique = {}
        for row in rows:
            if row['link_id'] in unique and unique[row['link_id']] != row:
                raise ValueError('same graph link identity has inconsistent provenance properties')
            unique[row['link_id']] = row
        edges[kind] = list(unique.values())
    return {'dataset_id': DATASET, 'link_version': LINK_VERSION,
        'locators': sorted(locators.values(), key=lambda x: x['locator_id']),
        'source_versions': sorted(versions.values(), key=lambda x: x['version_id']),
        'edges': edges, 'fingerprint': sha(canonical(links)),
        'matching_policy': links['source_version_policy']}


def local_inputs():
    return (readl(ROOT / 'output/chunks.jsonl'), load(ROOT / 'output/evidence.json'),
        readl(ROOT / 'output/embedded_records.jsonl'), load(ROOT / 'output/relations.json'),
        load(ROOT / 'output/source_catalog.json'))


def _live_identity(s, info):
    result = {**info, 'reachable': True, 'connection_settings': [dict(r) for r in s.run(
        'SHOW SETTINGS YIELD name,value WHERE name="server.directories.neo4j_home" RETURN name,value')]}
    validate_target(result)
    return {'database': info['database'], 'finance_home_verified': True,
            'home_id': FINANCE_HOME, 'credential_values_recorded': False}


def _snapshot(s):
    nodes, relationships, non_tech = {}, {}, {}
    labels = Counter()
    for row in s.run('MATCH (n) RETURN elementId(n) AS id,labels(n) AS labels,properties(n) AS props'):
        digest = sha(canonical([sorted(row['labels']), row['props']]))
        nodes[row['id']] = digest
        labels.update(row['labels'])
        if not any(label.startswith('Tech') for label in row['labels']):
            non_tech[row['id']] = digest
    for row in s.run('MATCH (a)-[r]->(b) RETURN elementId(r) AS id,elementId(a) AS start,'
                     'elementId(b) AS end,type(r) AS type,properties(r) AS props'):
        relationships[row['id']] = sha(canonical([row['start'], row['end'], row['type'], row['props']]))
    return {'nodes': nodes, 'relationships': relationships, 'non_tech': non_tech,
        'label_counts': dict(labels), 'node_count': len(nodes), 'relationship_count': len(relationships),
        'node_fingerprint': sha(canonical(nodes)), 'relationship_fingerprint': sha(canonical(relationships))}


def _summary(snapshot):
    return {key: value for key, value in snapshot.items() if key not in ['nodes', 'relationships', 'non_tech']} | {
        'non_tech_node_count': len(snapshot['non_tech']),
        'non_tech_fingerprint': sha(canonical(snapshot['non_tech']))}


def _preserved(before, after):
    nodes = all(after['nodes'].get(key) == value for key, value in before['nodes'].items())
    relationships = all(after['relationships'].get(key) == value for key, value in before['relationships'].items())
    core = all(before['label_counts'].get(key, 0) == after['label_counts'].get(key, 0) for key in CORE_LABELS)
    non_tech = before['non_tech'] == after['non_tech']
    if not all([nodes, relationships, core, non_tech]):
        raise RuntimeError('pre-existing graph changed; inspect concurrent writers before continuing')
    return {'all_preexisting_nodes_and_properties_preserved': nodes,
            'all_preexisting_relationships_and_properties_preserved': relationships,
            'core_label_counts_preserved': core, 'non_tech_nodes_unchanged': non_tech}


def _preflight(s, inputs):
    specs = [('TechChunk', 'chunk_id', inputs[0]), ('TechEvidence', 'evidence_id', inputs[1]),
             ('TechKeyRecord', 'record_id', inputs[2]), ('TechAssertion', 'relation_id', inputs[3]),
             ('TechSource', 'source_id', inputs[4])]
    for label, key, rows in specs:
        expected = {row[key]: canonical(row) for row in rows}
        actual = {row['key']: canonical(json.loads(row['payload'])) for row in s.run(
            f'MATCH (n:{label} {{dataset_id:$dataset}}) RETURN n.{key} AS key,n.payload AS payload', dataset=DATASET)}
        if actual != expected:
            raise RuntimeError(f'local/Neo4j {label} identities or payloads differ; no links written')
    missing = s.run('MATCH (a:TechAssertion {dataset_id:$dataset}) '
        'WHERE NOT EXISTS { MATCH (a)-[:TECH_SUBJECT]->(:TechEntity {dataset_id:$dataset}) } '
        'OR NOT EXISTS { MATCH (a)-[:TECH_OBJECT]->(:TechEntity {dataset_id:$dataset}) } '
        'OR NOT EXISTS { MATCH (a)-[:TECH_SUPPORTED_BY]->(:TechEvidence {dataset_id:$dataset}) } '
        'RETURN count(a) AS n', dataset=DATASET).single()['n']
    if missing:
        raise RuntimeError('existing assertion endpoint/evidence chain is incomplete; no links written')


def _write_plan(tx, plan):
    created_nodes = created_relationships = 0
    for label, key, rows in [('TechEvidenceLocator', 'locator_id', plan['locators']),
                             ('TechSourceVersion', 'version_id', plan['source_versions'])]:
        for offset in range(0, len(rows), 250):
            result = tx.run(f'UNWIND $rows AS row MERGE (n:{label} '
                f'{{dataset_id:$dataset,{key}:row.{key}}}) ON CREATE SET n += row,n.created_at=$now',
                rows=rows[offset:offset+250], dataset=DATASET, now=now()).consume()
            created_nodes += result.counters.nodes_created
    for kind, (start_label, start_key, end_label, end_key) in EDGE_SPECS.items():
        rows = plan['edges'][kind]
        for offset in range(0, len(rows), 250):
            result = tx.run(f'UNWIND $rows AS row '
                f'MATCH (a:{start_label} {{dataset_id:$dataset,{start_key}:row.{start_key}}}),'
                f'(b:{end_label} {{dataset_id:$dataset,{end_key}:row.{end_key}}}) '
                f'MERGE (a)-[r:{kind} {{dataset_id:$dataset,link_id:row.link_id}}]->(b) '
                'ON CREATE SET r += row,r.created_at=$now',
                rows=rows[offset:offset+250], dataset=DATASET, now=now()).consume()
            created_relationships += result.counters.relationships_created
    return {'nodes_created': created_nodes, 'relationships_created': created_relationships}


def _verify_plan(s, plan):
    actual_counts = {}
    for kind, (start_label, start_key, end_label, end_key) in EDGE_SPECS.items():
        expected = {row['link_id']: row for row in plan['edges'][kind]}
        rows = list(s.run(f'MATCH (a:{start_label} {{dataset_id:$dataset}})-[r:{kind} '
            f'{{dataset_id:$dataset,link_version:$version}}]->(b:{end_label} {{dataset_id:$dataset}}) '
            f'RETURN a.{start_key} AS start,b.{end_key} AS end,properties(r) AS props',
            dataset=DATASET, version=LINK_VERSION))
        if len(rows) != len(expected):
            raise RuntimeError(f'{kind} graph count does not match exact plan')
        for row in rows:
            want = expected.get(row['props'].get('link_id'))
            if not want or row['start'] != want[start_key] or row['end'] != want[end_key]:
                raise RuntimeError(f'{kind} endpoints do not match exact plan')
            if not properties_match(row['props'], want):
                raise RuntimeError(f'{kind} exact coordinate/hash/time properties differ')
        actual_counts[kind] = len(rows)
    for label, key, wanted in [('TechEvidenceLocator', 'locator_id', plan['locators']),
                               ('TechSourceVersion', 'version_id', plan['source_versions'])]:
        actual = {r['key']: r['props'] for r in s.run(f'MATCH (n:{label} '
            f'{{dataset_id:$dataset,link_version:$version}}) RETURN n.{key} AS key,properties(n) AS props',
            dataset=DATASET, version=LINK_VERSION)}
        if len(actual) != len(wanted) or any(not properties_match(actual.get(row[key], {}), row)
                                            for row in wanted):
            raise RuntimeError(f'{label} node hashes, locator coordinates or payloads differ')
        actual_counts[label] = len(actual)
    chains = [dict(r) for r in s.run('MATCH (k:TechKeyRecord {dataset_id:$dataset})'
        '-[:TECH_REPRESENTS {dataset_id:$dataset}]->(a:TechAssertion {dataset_id:$dataset})'
        '-[:TECH_SUPPORTED_BY]->(e:TechEvidence {dataset_id:$dataset})'
        '-[:TECH_LOCATED_AT {dataset_id:$dataset}]->(l:TechEvidenceLocator {dataset_id:$dataset})'
        '-[:TECH_OVERLAPS_CHUNK {dataset_id:$dataset}]->(c:TechChunk {dataset_id:$dataset})'
        '-[:TECH_IN_SOURCE_VERSION {dataset_id:$dataset}]->(v:TechSourceVersion {dataset_id:$dataset})'
        '-[:TECH_VERSION_OF {dataset_id:$dataset}]->(s:TechSource {dataset_id:$dataset}),'
        '(a)-[:TECH_SUBJECT]->(sub:TechEntity {dataset_id:$dataset}),'
        '(a)-[:TECH_OBJECT]->(obj:TechEntity {dataset_id:$dataset}) '
        'RETURN k.record_id AS record_id,a.relation_id AS relation_id,sub.entity_id AS subject_id,'
        'a.predicate AS predicate,obj.entity_id AS object_id,e.evidence_id AS evidence_id,'
        'l.locator_id AS locator_id,c.chunk_id AS chunk_id,v.source_sha256 AS source_sha256,'
        's.source_id AS source_id ORDER BY record_id,evidence_id,chunk_id LIMIT 5', dataset=DATASET)]
    represented = s.run('MATCH (k:TechKeyRecord {dataset_id:$dataset})'
        '-[:TECH_REPRESENTS {dataset_id:$dataset}]->(a:TechAssertion {dataset_id:$dataset})'
        '-[:TECH_SUPPORTED_BY]->(:TechEvidence {dataset_id:$dataset})'
        '-[:TECH_LOCATED_AT {dataset_id:$dataset}]->(:TechEvidenceLocator {dataset_id:$dataset})'
        '-[:TECH_OVERLAPS_CHUNK {dataset_id:$dataset}]->(:TechChunk {dataset_id:$dataset}) '
        'RETURN count(DISTINCT a) AS n', dataset=DATASET).single()['n']
    if represented != len(plan['edges']['TECH_REPRESENTS']):
        raise RuntimeError('not every reviewed relation card has a complete original-source chain')
    return {'counts': actual_counts, 'complete_assertion_chains': represented,
            'sample_chains': chains, 'all_exact_links_match_local_intervals': True}


def verify_links(config=None):
    config = config or ROOT / '.env'
    inputs = local_inputs()
    plan = prepare_rows(*inputs)
    driver, info = connect(config)
    try:
        with driver.session(database=info['database']) as s:
            identity = _live_identity(s, info)
            _preflight(s, inputs)
            return {'target': identity, 'dataset_id': DATASET, 'writes_executed': False,
                    'plan_fingerprint': plan['fingerprint'], **_verify_plan(s, plan), 'verified_at': now()}
    finally:
        driver.close()


def sync_links(config=None, report_path=None):
    """Add only this Tech plan, then prove a second MERGE is a no-op."""
    config = config or ROOT / '.env'
    validate_target(probe(config))  # Read-only confirmation before opening a write session.
    inputs = local_inputs()
    plan = prepare_rows(*inputs)
    driver, info = connect(config)
    try:
        with driver.session(database=info['database']) as s:
            identity = _live_identity(s, info)  # Recheck identity on the actual write connection.
            _preflight(s, inputs)
            before = _snapshot(s)
            first = s.execute_write(_write_plan, plan)
            mid = _snapshot(s)
            preservation = _preserved(before, mid)
            verification = _verify_plan(s, plan)
            second = s.execute_write(_write_plan, plan)
            after = _snapshot(s)
            _preserved(mid, after)
            if second != {'nodes_created': 0, 'relationships_created': 0} or mid['nodes'] != after['nodes'] or mid['relationships'] != after['relationships']:
                raise RuntimeError('second exact-link merge was not idempotent')
            result = {'target': identity, 'dataset_id': DATASET, 'link_version': LINK_VERSION,
                'plan_fingerprint': plan['fingerprint'], 'matching_policy': plan['matching_policy'],
                'before': _summary(before), 'after': _summary(after), 'first_run': first,
                'second_run': second, 'second_run_idempotent': True, **preservation,
                'verification': verification, 'writes_executed': True, 'old_payloads_rewritten': False,
                'delete_queries_executed': False, 'services_controlled': False,
                'database_or_dbms_created': False, 'executed_at': now()}
        dump(report_path or ROOT / 'output/graph_link_update_20261018.json', result)
        return result
    finally:
        driver.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Exact Tech provenance joins in existing Finance/neo4j only')
    parser.add_argument('--config', default=str(ROOT / '.env'))
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args()
    result = verify_links(args.config) if args.verify_only else sync_links(args.config, args.out)
    if args.verify_only and args.out:
        dump(args.out, result)
    print(json.dumps({key: result.get(key) for key in ['dataset_id', 'first_run', 'second_run',
        'second_run_idempotent', 'complete_assertion_chains', 'counts'] if key in result}, ensure_ascii=False))
