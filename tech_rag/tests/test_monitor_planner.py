"""Offline monitor plans must retain audited scope without shipping raw evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TMP = Path(__file__).resolve().parents[1] / 'state' / 'test_tmp'
TMP.mkdir(parents=True, exist_ok=True)


class MonitorPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from research import ResearchEngine
        cls.engine = ResearchEngine(index_path=':memory:')

    def planner(self, path, engine=None):
        self.assertIsNotNone(importlib.util.find_spec('monitor_planner'),
                             'Versioned offline monitor planner is missing')
        from monitor_planner import MonitorPlanner
        return MonitorPlanner(path, engine=engine or self.engine)

    def test_real_company_guides_are_distinct_and_sources_are_verified(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            guides = {ticker: planner.guide(ticker) for ticker in ('MU', 'TSM', 'NVDA')}
            serialized = {ticker: json.dumps(g['topics'], ensure_ascii=False) for ticker, g in guides.items()}
            self.assertIn('NAND', serialized['MU'])
            self.assertIn('晶圆', serialized['TSM'])
            self.assertIn('CUDA', serialized['NVDA'])
            self.assertNotEqual(serialized['MU'], serialized['NVDA'])
            for guide in guides.values():
                self.assertEqual(len(guide['topics']), 6)
                audit = planner.get_guide(guide['guide_id'])
                self.assertTrue(audit['sources'])
                self.assertTrue(audit['relations'])
                for source in audit['sources']:
                    self.assertTrue(source['round_trip_verified'])
                    self.assertEqual(len(source['source_sha256']), 64)
                    self.assertNotIn(source['text'], guide['agent_prompt'])
                self.assertNotIn('https://', guide['agent_prompt'])
                self.assertNotRegex(guide['agent_prompt'], r'EV_[a-z0-9]+|C_[a-z0-9]+')

    def test_relation_limits_and_priority_are_not_fact_confidence(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            guide = self.planner(tmp).guide('NVDA')
            text = json.dumps(guide['topics'], ensure_ascii=False)
            self.assertIn('不推断全部为HBM', text)
            self.assertIn('保持匿名', text)
            for topic in guide['topics']:
                for key in ('id', 'title', 'search_objects', 'keywords', 'signals', 'questions',
                            'impact_path', 'priority', 'priority_reason', 'trigger', 'relationship_status'):
                    self.assertIn(key, topic)
                self.assertIsInstance(topic['search_objects'], list)
                self.assertGreaterEqual(len(topic['priority_factors']), 3)
                self.assertIn('核实优先级', topic['priority_reason'])

    def test_handoff_is_compact_while_audit_keeps_full_relation_conditions(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            guide = planner.guide('NVDA')
            self.assertLessEqual(len(guide['agent_prompt']), 2000)
            self.assertIn('不推断全部为HBM', guide['agent_prompt'])
            self.assertIn('保持匿名', guide['agent_prompt'])
            self.assertIn('H200', guide['agent_prompt'])
            audit = planner.get_guide(guide['guide_id'])
            self.assertTrue(all(r['conditions'] for r in audit['relations']))
            self.assertIn('Memory procurement, FY2026; exact generation, share and purchase amounts not disclosed',
                          json.dumps(guide['topics']))

    def test_prompt_keeps_theme_lines_and_each_theme_has_actionable_checks(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            guide = self.planner(tmp).guide('NVDA')
            self.assertGreaterEqual(len(guide['agent_prompt'].splitlines()), 8)
            for topic in guide['topics']:
                line = next(row for row in guide['agent_prompt'].splitlines()
                            if row.startswith(topic['title']))
                self.assertIn('问：', line)
                self.assertIn('触发：', line)
                self.assertIn('依据：', line)

    def test_search_queries_are_company_specific_and_anonymous_clients_stay_unidentified(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            guides = {ticker: planner.guide(ticker) for ticker in ('NVDA', 'MU', 'TSM')}
            downstream = {ticker: next(t for t in guide['topics'] if t['category'] == 'downstream')
                          for ticker, guide in guides.items()}
            nvda = downstream['NVDA']
            self.assertIn('客户集中度', ' '.join(nvda['search_objects']))
            self.assertIn('AI基础设施/GPU/数据中心开支', ' '.join(nvda['search_objects']))
            self.assertNotIn('晶圆代工客户', ' '.join(nvda['search_objects']))
            self.assertNotIn('Direct Customer A', ' '.join(nvda['search_objects']))
            self.assertTrue(any('Customer A' in obj or '匿名' in obj for obj in nvda['background_objects']))
            self.assertIn('GPU', nvda['search_query'])
            self.assertIn('data center spending', nvda['search_query'])
            self.assertIn('DRAM', downstream['MU']['search_query'])
            self.assertIn('NAND', downstream['MU']['search_query'])
            self.assertNotIn('foundry', downstream['MU']['search_query'])
            self.assertIn('wafer', downstream['TSM']['search_query'])
            for guide in guides.values():
                for topic in guide['topics']:
                    self.assertIsInstance(topic['search_query'], str)
                    self.assertTrue(topic['search_query'])
                    self.assertFalse(set(topic['keywords']) & set(topic['relationship_types']))

    def test_cache_refresh_retains_old_version_and_survives_new_instance(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            first = planner.guide('MU')
            cached = self.planner(tmp).guide('MU')
            self.assertEqual(cached, first)
            refreshed = planner.guide('MU', refresh=True)
            self.assertEqual(refreshed['version'], first['version'] + 1)
            self.assertNotEqual(refreshed['guide_id'], first['guide_id'])
            self.assertEqual(planner.get_guide(first['guide_id'])['guide'], first)

    def test_changed_knowledge_invalidates_cache_without_restarting_versions(self):
        from types import SimpleNamespace
        with TemporaryDirectory(dir=TMP) as tmp:
            first = self.planner(tmp).guide('MU')
            changed = SimpleNamespace(tools=self.engine.tools, e2c=self.engine.e2c,
                chunks_by_id=self.engine.chunks_by_id, verify_chunk=self.engine.verify_chunk,
                fingerprint=lambda at, historical: 'changed-reviewed-knowledge')
            second = self.planner(tmp, changed).guide('MU')
            self.assertNotEqual(first['guide_id'], second['guide_id'])
            self.assertGreater(second['version'], first['version'])

    def test_unverified_originals_do_not_yield_named_relationships(self):
        from types import SimpleNamespace
        bad = SimpleNamespace(tools=self.engine.tools, e2c=self.engine.e2c,
                              chunks_by_id=self.engine.chunks_by_id,
                              verify_chunk=lambda row: (False, 'original_version_hash_mismatch'),
                              fingerprint=self.engine.fingerprint)
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp, bad)
            guide = planner.guide('NVDA')
            self.assertEqual(guide['status'], 'insufficient')
            self.assertEqual(planner.get_guide(guide['guide_id'])['sources'], [])
            self.assertNotIn('Micron', json.dumps(guide['topics']))
            self.assertTrue(all(t['relationship_status'] == '待核实' for t in guide['topics']))

    def test_historical_guide_does_not_import_future_relations(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            guide = planner.guide('MU', '2025-01-01T00:00:00+00:00')
            audit = planner.get_guide(guide['guide_id'])
            self.assertTrue(all(s['available_at'] <= '2025-01-01T00:00:00+00:00'
                                for s in audit['sources']))
            self.assertNotIn('HBM4E', json.dumps(guide['topics']))

    def test_event_task_selects_only_event_related_paths_and_has_no_answers(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            event = {'title': 'CoWoS packaging capacity review',
                     'description': 'Unverified report of additional packaging supply',
                     'url': 'https://example.com/event', 'published_at': '2026-10-20T12:00:00+00:00'}
            task = planner.event_task('NVDA', event)
            self.assertEqual(task['status'], 'pending_research')
            self.assertEqual(len(task['instructions']), 9)
            self.assertTrue(all(step['result'] is None for step in task['instructions']))
            self.assertIn('严格早于 first_public_at', task['agent_prompt'])
            self.assertNotIn('CUDA', task['agent_prompt'])
            self.assertNotIn('https://', task['agent_prompt'])
            self.assertLess(len(task['related_topic_ids']), 6)
            files = list((Path(tmp) / 'events').glob('*.json'))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['event'], event)

    def test_inputs_and_lookup_paths_are_rejected_before_use(self):
        with TemporaryDirectory(dir=TMP) as tmp:
            planner = self.planner(tmp)
            for ticker in ('../../NVDA', 'AAPL', None, 'NVDA;'):
                with self.assertRaises(ValueError): planner.guide(ticker)
            for at in ('2026-10-22', '2026-10-22T10:00:00', 123):
                with self.assertRaises(ValueError): planner.guide('NVDA', at)
            with self.assertRaises(ValueError): planner.get_guide('../config')
            with self.assertRaises(ValueError): planner.event_task('NVDA', {'title':'x','url':'file:///secret'})
            with self.assertRaises(ValueError): planner.event_task('NVDA', {'title':'x','published_at':'2026-10-22'})
            with self.assertRaises(ValueError): planner.event_task('NVDA', {'title':'x','published_at':'current'})


if __name__ == '__main__':
    unittest.main()
