"""Safe target and exact provenance graph-plan regressions (no credentials)."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ProvenanceGraphPlanTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('neo4j_links'),
                             'missing precise Neo4j provenance link implementation')
        import neo4j_links
        return neo4j_links

    def fixture(self):
        chunks = [dict(chunk_id='c1', source_id='s', source_sha256='hash', pdf_page=1,
                       paragraph=None, char_start=0, char_end=10, available_at='2024-01-01T00:00:00+00:00',
                       historical_eligible=True),
                  dict(chunk_id='c2', source_id='s', source_sha256='hash', pdf_page=1,
                       paragraph=None, char_start=8, char_end=30, available_at='2024-01-01T00:00:00+00:00',
                       historical_eligible=True),
                  dict(chunk_id='unrelated', source_id='s', source_sha256='hash', pdf_page=2,
                       paragraph=None, char_start=0, char_end=30, available_at='2024-01-01T00:00:00+00:00',
                       historical_eligible=True)]
        evidence = [dict(evidence_id='e', source_id='s', source_sha256='hash', pdf_page=1,
                         paragraph=None, char_start=5, char_end=20,
                         available_at='2024-01-01T00:00:00+00:00', historical_eligible=True)]
        records = [dict(record_id='k', relation_ids=['r'])]
        relations = [dict(relation_id='r', subject_id='MU', object_id='TSM')]
        sources = [dict(source_id='s', sha256='hash', available_at='2024-01-01T00:00:00+00:00')]
        return chunks, evidence, records, relations, sources

    def test_target_is_exact_finance_home_and_neo4j_database(self):
        mod = self.module()
        probe = dict(database='neo4j', reachable=True, connection_settings=[
            dict(name='server.directories.neo4j_home', value='C:\\data\\dbms-164b5ace-03b4-45cd-bc0f-ac9c533d6ef4')])
        self.assertTrue(mod.validate_target(probe))
        for change in [dict(database='finance'), dict(connection_settings=[dict(name='server.directories.neo4j_home', value='C:\\other-db')])]:
            with self.assertRaises(RuntimeError):
                mod.validate_target({**probe, **change})

    def test_plan_only_links_exact_overlapping_chunks_and_keeps_version(self):
        mod = self.module()
        plan = mod.prepare_rows(*self.fixture())
        links = plan['edges']['TECH_EVIDENCE_COVERS']
        self.assertEqual([(r['chunk_id'], r['overlap_start'], r['overlap_end']) for r in links],
                         [('c1', 5, 10), ('c2', 8, 20)])
        self.assertEqual(plan['locators'][0]['coverage_fraction'], 1.0)
        self.assertEqual(len(plan['source_versions']), 1)
        self.assertEqual(plan['source_versions'][0]['source_sha256'], 'hash')
        self.assertEqual(plan['edges']['TECH_REPRESENTS'][0]['relation_id'], 'r')
        self.assertTrue(all(r['dataset_id'] == 'tech_company_v1'
                            for rows in plan['edges'].values() for r in rows))
        json.dumps(plan)

    def test_forged_link_without_exact_source_version_interval_is_rejected(self):
        mod = self.module()
        from provenance import build_links
        chunks, evidence, records, relations, sources = self.fixture()
        links = build_links(chunks, evidence, records, relations)
        links['evidence_chunk_links'][0]['chunk_id'] = 'unrelated'
        with self.assertRaises(ValueError):
            mod.prepare_rows(chunks, evidence, records, relations, sources, links=links)

    def test_local_source_hash_mismatch_is_rejected_before_graph_write(self):
        mod = self.module()
        chunks, evidence, records, relations, sources = self.fixture()
        sources[0]['sha256'] = 'another-version'
        with self.assertRaises(ValueError):
            mod.prepare_rows(chunks, evidence, records, relations, sources)

    def test_shared_exact_locator_deduplicates_locator_edges_but_keeps_evidence_edges(self):
        mod = self.module()
        chunks, evidence, records, relations, sources = self.fixture()
        evidence.append({**evidence[0], 'evidence_id': 'e2'})
        plan = mod.prepare_rows(chunks, evidence, records, relations, sources)
        self.assertEqual(len(plan['locators']), 1)
        self.assertEqual(len(plan['edges']['TECH_LOCATED_AT']), 2)
        self.assertEqual(len(plan['edges']['TECH_OVERLAPS_CHUNK']), 2)
        self.assertEqual(len(plan['edges']['TECH_EVIDENCE_COVERS']), 4)

    def test_graph_property_verification_rejects_a_changed_null_coordinate(self):
        mod = self.module()
        self.assertTrue(hasattr(mod, 'properties_match'), 'missing exact graph-property comparison')
        self.assertTrue(mod.properties_match({'pdf_page': 1, 'created_at': 'now'},
                                             {'pdf_page': 1, 'paragraph': None}))
        self.assertFalse(mod.properties_match({'pdf_page': 1, 'paragraph': 2},
                                              {'pdf_page': 1, 'paragraph': None}))

    def test_explicit_card_reference_to_missing_assertion_is_rejected(self):
        mod = self.module()
        chunks, evidence, records, relations, sources = self.fixture()
        records[0]['relation_ids'].append('missing')
        with self.assertRaises(ValueError):
            mod.prepare_rows(chunks, evidence, records, relations, sources)


if __name__ == '__main__':
    unittest.main()
