"""Real exchange-session scheduling, independent from network and model calls."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MonitorScheduleTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('monitor_schedule'), 'monitor scheduling missing')
        import monitor_schedule
        return monitor_schedule

    def config(self, at='2026-10-22T00:00:00+00:00'):
        return self.api().default_config(' nvda ', now=at)

    def test_defaults_are_independent_candidate_settings(self):
        config = self.config()
        self.assertEqual(config['ticker'], 'NVDA')
        self.assertEqual(config['cadence']['trading_minutes'], 30)
        self.assertEqual(config['retrieval']['overlap_minutes'], 120)
        self.assertEqual(config['analysis']['event']['trading_days'], [0, 1, 5])
        config['retrieval']['overlap_minutes'] = 40
        self.assertEqual(self.config()['retrieval']['overlap_minutes'], 120)
        self.assertNotIn('active', config)

    def test_known_security_defaults_select_its_real_exchange(self):
        m = self.api()
        self.assertEqual(m.default_config('TSM', now='2026-10-22T00:00:00Z')['market'], 'XNYS')
        self.assertEqual(m.default_config('MU', now='2026-10-22T00:00:00Z')['market'], 'XNAS')
        self.assertEqual(m.default_config('NVDA', now='2026-10-22T00:00:00Z')['market'], 'XNAS')

    def test_optional_topic_selection_and_priorities_are_preserved_without_default_changes(self):
        m = self.api()
        config = self.config()
        self.assertNotIn('topic_ids', config)
        self.assertNotIn('topic_priorities', config)
        config.update(topic_ids=['demand', 'supply'], topic_priorities={'demand': '高', 'supply': '低'})
        result = m.validate_config(config)
        self.assertEqual(result['topic_ids'], ['demand', 'supply'])
        self.assertEqual(result['topic_priorities'], {'demand': '高', 'supply': '低'})
        result['topic_ids'].append('other')
        self.assertEqual(config['topic_ids'], ['demand', 'supply'])

    def test_invalid_topic_selections_and_priorities_are_rejected(self):
        m = self.api()
        invalid = [
            {'topic_ids': []}, {'topic_ids': ['one', 'one']}, {'topic_ids': 'one'},
            {'topic_ids': ['']}, {'topic_ids': ['   ']}, {'topic_ids': [' one']},
            {'topic_ids': [1]}, {'topic_ids': [[]]}, {'topic_ids': ['a' * 101]},
            {'topic_ids': [f'topic-{i}' for i in range(31)]}, {'topic_ids': None},
            {'topic_priorities': []}, {'topic_priorities': None},
            {'topic_priorities': {'one': 'urgent'}}, {'topic_priorities': {'one': True}},
            {'topic_priorities': {'': '高'}}, {'topic_priorities': {'  ': '中'}},
            {'topic_priorities': {'a' * 101: '低'}}, {'topic_priorities': {1: '高'}},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                config = self.config()
                config.update(fields)
                with self.assertRaises(ValueError):
                    m.validate_config(config)

    def test_topic_limits_accept_thirty_ids_and_all_priority_levels(self):
        m = self.api()
        config = self.config()
        config['topic_ids'] = ['a' * 100] + [f'topic-{i}' for i in range(29)]
        config['topic_priorities'] = {'a' * 100: '高', 'topic-0': '中', 'topic-1': '低'}
        self.assertEqual(len(m.validate_config(config)['topic_ids']), 30)

    def test_validation_rejects_unbounded_or_ambiguous_configuration(self):
        m = self.api()
        changes = [
            ('cadence', 'trading_minutes', 0), ('cadence', 'off_hours_minutes', True),
            ('cadence', 'guide_weekday', 7), ('cadence', 'guide_time', '25:00'),
            ('retrieval', 'lookback_hours', -1), ('budgets', 'llm_calls_per_day', -1),
            ('analysis', 'event', {'enabled': True, 'trading_days': [1, -1]}),
            ('lifecycle', 'start_at', '2026-10-22T00:00:00'),
        ]
        for section, field, value in changes:
            with self.subTest(field=field):
                config = self.config()
                config[section][field] = value
                with self.assertRaises(ValueError):
                    m.validate_config(config)
        for field, value in [('market', 'WEEKDAYS'), ('display_timezone', 'Mars/Now'), ('ticker', '')]:
            config = self.config()
            config[field] = value
            with self.assertRaises(ValueError):
                m.validate_config(config)

    def test_missing_calendar_blocks_instead_of_synthesizing_workdays(self):
        m = self.api()
        with patch.object(m, '_load_exchange_calendars', side_effect=ImportError('not installed')):
            result = m.preview_schedule(self.config(), now='2026-10-22T13:40:00Z')
        self.assertEqual(result['next_runs'], [])
        self.assertFalse(result['calendar']['ready'])
        self.assertTrue(result['errors'])

    def test_real_market_calendar_handles_early_close_and_display_zone(self):
        m = self.api()
        config = self.config('2026-11-27T00:00:00Z')
        result = m.preview_schedule(config, now='2026-11-27T17:45:00Z')
        self.assertEqual(result['errors'], [])
        self.assertEqual([(x['kind'], x['at_utc']) for x in result['next_runs']], [
            ('search', '2026-11-27T18:00:00+00:00'),
            ('fundamental', '2026-11-27T19:00:00+00:00'),
            ('search', '2026-11-27T20:00:00+00:00')])
        self.assertEqual(result['next_runs'][0]['at_display'], '2026-11-28T02:00:00+08:00')
        self.assertTrue(result['calendar']['ready'])
        self.assertEqual(result['calendar']['requested'], 'XNAS')
        self.assertIn(result['calendar']['resolved'], ['XNAS', 'XNYS'])

    def test_holiday_and_dst_use_actual_session_close(self):
        m = self.api()
        config = self.config('2026-01-01T00:00:00Z')
        self.assertEqual(m.next_run(config, 'fundamental', '2026-11-26T00:00:00Z'),
                         '2026-11-27T19:00:00+00:00')
        self.assertEqual(m.next_run(config, 'fundamental', '2026-03-06T22:01:00Z'),
                         '2026-03-09T21:00:00+00:00')
        self.assertEqual(m.next_run(config, 'fundamental', '2026-10-30T21:01:00Z'),
                         '2026-11-02T22:00:00+00:00')

    def test_weekly_guide_is_explicit_local_weekday_and_time(self):
        m = self.api()
        config = self.config('2026-01-01T00:00:00Z')
        self.assertEqual(m.next_run(config, 'guide_maintenance', '2026-10-25T23:59:00Z'),
                         '2026-10-26T01:00:00+00:00')
        config['cadence'].update(guide_timezone='America/New_York', guide_weekday=0, guide_time='09:00')
        self.assertEqual(m.next_run(config, 'guide_maintenance', '2026-10-30T21:00:00Z'),
                         '2026-11-02T14:00:00+00:00')

    def test_lifecycle_end_prevents_future_jobs_and_analysis_does_not_change_search(self):
        m = self.api()
        config = self.config('2026-10-22T00:00:00Z')
        other = copy.deepcopy(config)
        other['analysis']['event']['trading_days'] = [7]
        other['retrieval']['overlap_minutes'] = 10
        self.assertEqual(m.next_run(config, 'search', '2026-10-22T14:07:00Z'),
                         m.next_run(other, 'search', '2026-10-22T14:07:00Z'))
        config['lifecycle']['end_at'] = '2026-10-22T14:08:00Z'
        self.assertIsNone(m.next_run(config, 'search', '2026-10-22T14:07:00Z'))

    def test_followups_count_real_sessions_and_use_early_close(self):
        m = self.api()
        self.assertTrue(hasattr(m, 'followup_times'), 'trading-day followup scheduler missing')
        config = self.config('2026-01-01T00:00:00Z')
        config['followup']['trading_days'] = [1, 2]
        self.assertEqual(m.followup_times(config, '2026-11-25T18:00:00Z'), [
            '2026-11-27T19:00:00+00:00', '2026-11-30T22:00:00+00:00'])
        self.assertEqual(m.followup_schedule(config, '2026-11-25T18:00:00Z')['policy'],
                         'Nth exchange session after anchor market date; actual close plus close_delay_minutes')

    def test_followups_never_schedule_before_disclosure_or_after_lifecycle_end(self):
        m = self.api()
        self.assertTrue(hasattr(m, 'followup_times'), 'trading-day followup scheduler missing')
        config = self.config('2026-01-01T00:00:00Z')
        config['followup']['trading_days'] = [0, 1]
        config['lifecycle']['end_at'] = '2026-11-30T21:00:00Z'
        self.assertEqual(m.followup_times(config, '2026-11-27T19:01:00Z'), [])


if __name__ == '__main__':
    unittest.main()
