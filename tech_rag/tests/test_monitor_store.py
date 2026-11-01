"""Persistent confirmation and single-worker job ownership without real execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TMP = Path(__file__).resolve().parents[1] / 'state' / 'monitor_validation' / 'tmp'
TMP.mkdir(parents=True, exist_ok=True)
READY = {key: {'ready': True} for key in ['calendar', 'news', 'agent', 'market']}
NOW = '2026-10-22T14:07:00+00:00'


class MonitorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.temp.cleanup)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('monitor_store'), 'monitor store missing')
        from monitor_store import MonitorStore
        from monitor_schedule import default_config
        return MonitorStore, default_config

    def store(self):
        return self.api()[0](Path(self.temp.name) / 'monitor.sqlite3')

    def draft(self, store, ticker='NVDA'):
        return store.save_draft(self.api()[1](ticker, now=NOW), 'guide-v1')

    def active(self, store):
        draft = self.draft(store)
        preview = store.preview(draft['draft_id'], READY, now=NOW)
        return store.confirm(draft['draft_id'], preview['confirmation_hash'], READY, now=NOW)

    def test_draft_is_persistent_and_never_claimed_before_confirmation(self):
        store = self.store()
        draft = self.draft(store)
        self.assertEqual(self.store().get_draft(draft['draft_id'])['config']['ticker'], 'NVDA')
        self.assertIsNone(store.claim_due(now='2026-10-23T00:00:00Z'))
        self.assertEqual(store.list_plans()[0]['status'], 'draft')

    def test_preview_summary_is_required_and_dependency_loss_blocks_confirm(self):
        store = self.store()
        draft = self.draft(store)
        with self.assertRaises(ValueError):
            store.confirm(draft['draft_id'], 'unseen-hash', READY, now=NOW)
        preview = store.preview(draft['draft_id'], READY, now=NOW)
        self.assertEqual(len(preview['next_runs']), 3)
        self.assertEqual(preview['blockers'], [])
        dependencies = dict(READY, agent={'ready': False, 'reason': 'not connected'})
        with self.assertRaises(ValueError):
            store.confirm(draft['draft_id'], preview['confirmation_hash'], dependencies, now=NOW)
        dependencies = dict(READY, market={'ready': False})
        active = store.confirm(draft['draft_id'], preview['confirmation_hash'], dependencies, now=NOW)
        self.assertEqual(active['status'], 'active')

    def test_confirmation_is_idempotent_by_ticker_market_and_changes_stay_draft(self):
        store = self.store()
        active = self.active(store)
        duplicate = self.draft(store)
        preview = store.preview(duplicate['draft_id'], READY, now=NOW)
        same = store.confirm(duplicate['draft_id'], preview['confirmation_hash'], READY, now=NOW)
        self.assertEqual(same['plan_id'], active['plan_id'])
        self.assertEqual(same['version'], 1)
        changed = dict(active['config'])
        changed['retrieval'] = dict(changed['retrieval'], overlap_minutes=45)
        draft = store.save_draft(changed, 'guide-v2', plan_id=active['plan_id'])
        preview = store.preview(draft['draft_id'], READY, now=NOW)
        self.assertTrue(any('overlap_minutes' in item['field'] for item in preview['changes']))
        self.assertEqual(store.get_plan(active['plan_id'])['config']['retrieval']['overlap_minutes'], 120)
        updated = store.confirm(draft['draft_id'], preview['confirmation_hash'], READY, now=NOW)
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['config']['retrieval']['overlap_minutes'], 45)

    def test_old_preview_cannot_replace_a_newer_confirmed_version(self):
        store = self.store()
        active = self.active(store)
        d1 = store.save_draft(active['config'], 'guide-v2', active['plan_id'])
        d2 = store.save_draft(active['config'], 'guide-v3', active['plan_id'])
        p1 = store.preview(d1['draft_id'], READY, now=NOW)
        p2 = store.preview(d2['draft_id'], READY, now=NOW)
        store.confirm(d2['draft_id'], p2['confirmation_hash'], READY, now=NOW)
        with self.assertRaises(ValueError):
            store.confirm(d1['draft_id'], p1['confirmation_hash'], READY, now=NOW)

    def test_pause_resume_end_and_no_parallel_claim_even_across_store_instances(self):
        store = self.store()
        active = self.active(store)
        pid = active['plan_id']
        store.action(pid, 'pause')
        self.assertIsNone(store.claim_due(now='2026-10-22T15:00:00Z'))
        store.action(pid, 'resume')
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: self.store().claim_due(now='2026-10-22T15:00:00Z'), range(2)))
        self.assertEqual(sum(job is not None for job in jobs), 1)
        store.action(pid, 'end')
        self.assertIsNone(store.claim_due(now='2026-10-23T00:00:00Z'))
        with self.assertRaises(ValueError):
            store.action(pid, 'resume')

    def test_failed_jobs_retry_three_attempts_and_only_success_advances_search_watermark(self):
        store = self.store()
        active = self.active(store)
        job = store.claim_due(now='2026-10-22T14:30:00Z')
        self.assertEqual(job['kind'], 'search')
        self.assertEqual(job['attempt'], 1)
        self.assertIsNone(job['watermark'])
        store.finish_run(job['run_id'], 'failed', {'count': 0}, watermark='bad', error='offline', now='2026-10-22T14:30:01Z')
        self.assertIsNone(store.claim_due(now='2026-10-22T14:30:10Z'))
        retry = store.claim_due(now='2026-10-22T14:31:01Z')
        self.assertEqual(retry['attempt'], 2)
        self.assertIsNone(retry['watermark'])
        store.finish_run(retry['run_id'], 'failed', {}, now='2026-10-22T14:31:02Z')
        third = store.claim_due(now='2026-10-22T14:33:02Z')
        self.assertEqual(third['attempt'], 3)
        store.finish_run(third['run_id'], 'success', {'events': []}, watermark='2026-10-22T14:30:00Z', now='2026-10-22T14:33:03Z')
        self.assertEqual(store.get_plan(active['plan_id'])['watermark'], '2026-10-22T14:30:00+00:00')
        runs = store.list_runs(active['plan_id'])
        self.assertEqual(len(runs), 3)
        self.assertIn('result', runs[0])
        self.assertIn('payload', runs[0])

    def test_expired_lease_is_recovered_and_late_owner_cannot_finish(self):
        store = self.store()
        self.active(store)
        job = store.claim_due(now='2026-10-22T14:30:00Z')
        recovered = self.store().claim_due(now='2026-10-22T15:01:00Z')
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered['attempt'], 2)
        self.assertNotEqual(recovered['run_id'], job['run_id'])
        with self.assertRaises(ValueError):
            store.finish_run(job['run_id'], 'success', {}, now='2026-10-22T15:01:01Z')

    def test_event_followups_are_idempotent_and_do_not_move_search_watermark(self):
        store = self.store()
        active = self.active(store)
        first = store.enqueue_followup(active['plan_id'], 'event-one', '2026-10-22T14:08:00Z')
        second = store.enqueue_followup(active['plan_id'], 'event-one', '2026-10-22T14:08:00Z')
        self.assertEqual(first, second)
        job = store.claim_due(now='2026-10-22T14:08:00Z')
        self.assertEqual(job['kind'], 'event_followup')
        self.assertEqual(job['payload']['event_id'], 'event-one')
        store.finish_run(job['run_id'], 'success', {'review': 'unknown'}, watermark='2026-10-22T14:08:00Z', now='2026-10-22T14:08:01Z')
        self.assertIsNone(store.get_plan(active['plan_id'])['watermark'])

    def test_identical_drafts_confirm_concurrently_to_one_version(self):
        store = self.store()
        first, second = self.draft(store), self.draft(store)
        p1, p2 = store.preview(first['draft_id'], READY, now=NOW), store.preview(second['draft_id'], READY, now=NOW)
        a = store.confirm(first['draft_id'], p1['confirmation_hash'], READY, now=NOW)
        b = store.confirm(second['draft_id'], p2['confirmation_hash'], READY, now=NOW)
        self.assertEqual(a['plan_id'], b['plan_id'])
        self.assertEqual(b['version'], 1)

    def test_third_failure_exhausts_retry_and_next_scheduled_search_can_run(self):
        store = self.store()
        self.active(store)
        for at, finished in [('2026-10-22T14:30:00Z', '2026-10-22T14:30:01Z'),
                             ('2026-10-22T14:31:01Z', '2026-10-22T14:31:02Z'),
                             ('2026-10-22T14:33:02Z', '2026-10-22T14:33:03Z')]:
            run = store.claim_due(now=at)
            store.finish_run(run['run_id'], 'failed', {}, now=finished)
        self.assertIsNone(store.claim_due(now='2026-10-22T14:50:00Z'))
        next_job = store.claim_due(now='2026-10-22T15:00:00Z')
        self.assertEqual(next_job['attempt'], 1)
        self.assertEqual(next_job['scheduled_at'], '2026-10-22T15:00:00+00:00')

    def test_zero_llm_budget_allows_unavailable_agent_and_dependency_shape_is_safe(self):
        store = self.store()
        config = self.api()[1]('NVDA', now=NOW)
        config['budgets']['llm_calls_per_day'] = 0
        draft = store.save_draft(config, 'guide-one')
        deps = dict(READY, agent=None)
        preview = store.preview(draft['draft_id'], deps, now=NOW)
        self.assertEqual(preview['blockers'], [])
        active = store.confirm(draft['draft_id'], preview['confirmation_hash'], deps, now=NOW)
        self.assertEqual(active['status'], 'active')
        deps['news'] = None
        self.assertTrue(store.preview(draft['draft_id'], deps, now=NOW)['blockers'])

    def test_pause_during_run_keeps_immutable_job_and_blocks_further_claims(self):
        store = self.store()
        active = self.active(store)
        run = store.claim_due(now='2026-10-22T14:30:00Z')
        store.action(active['plan_id'], 'pause')
        store.finish_run(run['run_id'], 'success', {}, watermark='2026-10-22T14:30:00Z', now='2026-10-22T14:31:00Z')
        self.assertEqual(store.get_plan(active['plan_id'])['status'], 'paused')
        self.assertIsNone(store.claim_due(now='2026-10-22T15:00:00Z'))

    def test_global_claim_limit_applies_to_different_tickers(self):
        store = self.store()
        self.active(store)
        draft = self.draft(store, 'TSM')
        p = store.preview(draft['draft_id'], READY, now=NOW)
        store.confirm(draft['draft_id'], p['confirmation_hash'], READY, now=NOW)
        run = store.claim_due(now='2026-10-22T14:30:00Z')
        self.assertIsNotNone(run)
        self.assertIsNone(self.store().claim_due(now='2026-10-22T14:30:01Z'))

    def test_old_version_failed_running_job_is_not_retried_after_new_confirmation(self):
        store = self.store()
        active = self.active(store)
        running = store.claim_due(now='2026-10-22T14:30:00Z')
        changed = active['config']
        changed['cadence']['trading_minutes'] = 60
        draft = store.save_draft(changed, 'guide-v2', active['plan_id'])
        p = store.preview(draft['draft_id'], READY, now='2026-10-22T14:31:00Z')
        store.confirm(draft['draft_id'], p['confirmation_hash'], READY, now='2026-10-22T14:31:00Z')
        store.finish_run(running['run_id'], 'failed', {}, now='2026-10-22T14:31:01Z')
        self.assertIsNone(store.claim_due(now='2026-10-22T14:32:01Z'))

    def test_expired_old_version_job_is_cancelled_after_new_confirmation(self):
        store = self.store()
        active = self.active(store)
        store.claim_due(now='2026-10-22T14:30:00Z')
        changed = active['config']
        changed['cadence']['trading_minutes'] = 60
        draft = store.save_draft(changed, 'guide-v2', active['plan_id'])
        p = store.preview(draft['draft_id'], READY, now='2026-10-22T14:31:00Z')
        store.confirm(draft['draft_id'], p['confirmation_hash'], READY, now='2026-10-22T14:31:00Z')
        self.assertIsNone(store.claim_due(now='2026-10-22T14:46:00Z'))

    def test_new_preview_invalidates_previous_confirmation_hash(self):
        store = self.store()
        draft = self.draft(store)
        old = store.preview(draft['draft_id'], READY, now=NOW)
        current = store.preview(draft['draft_id'], READY, now='2026-10-22T14:08:00Z')
        self.assertNotEqual(old['confirmation_hash'], current['confirmation_hash'])
        with self.assertRaises(ValueError):
            store.confirm(draft['draft_id'], old['confirmation_hash'], READY, now=NOW)


if __name__ == '__main__':
    unittest.main()
