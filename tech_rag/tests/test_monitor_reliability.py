from pathlib import Path
from datetime import timedelta
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor_store import MonitorStore
from monitor_schedule import default_config,utc_datetime

class LeaseTests(unittest.TestCase):
    def active(self,root):
        store=MonitorStore(Path(root)/'plans.sqlite3');at='2026-10-22T14:00:00Z'
        config=default_config('NVDA',at);config['budgets']['llm_calls_per_day']=0
        dependencies=dict(calendar=dict(ready=True),news=dict(ready=True))
        draft=store.save_draft(config,'synthetic-guide')
        preview=store.preview(draft['draft_id'],dependencies,now=at)
        plan=store.confirm(draft['draft_id'],preview['confirmation_hash'],dependencies,now=at)
        run=store.claim_due('2026-10-22T15:00:00Z')
        return store,plan,run
    def test_lease_renewal_excludes_second_owner(self):
        with tempfile.TemporaryDirectory() as td:
            store,plan,run=self.active(td);self.assertIsNotNone(run)
            store.renew_lease(run['run_id'],'2026-10-22T15:14:00Z')
            self.assertIsNone(store.claim_due('2026-10-22T15:16:00Z'))
            store.assert_owner(run['run_id'],'2026-10-22T15:16:00Z')
    def test_expired_owner_cannot_resume(self):
        with tempfile.TemporaryDirectory() as td:
            store,plan,run=self.active(td)
            recovered=store.claim_due('2026-10-22T15:16:00Z')
            self.assertIsNotNone(recovered)
            with self.assertRaises(ValueError):store.renew_lease(run['run_id'],'2026-10-22T15:17:00Z')
    def test_daily_budget_defers_instead_of_fast_retry(self):
        with tempfile.TemporaryDirectory() as td:
            store,plan,run=self.active(td)
            store.finish_run(run['run_id'],'deferred',{'status':'budget_exhausted'},now='2026-10-22T15:01:00Z')
            with store._db() as db:
                job=db.execute('SELECT due_at,attempt FROM monitor_jobs WHERE job_id=(SELECT job_id FROM monitor_runs WHERE run_id=?)',(run['run_id'],)).fetchone()
            self.assertEqual(job['due_at'],'2026-10-23T00:00:00+00:00');self.assertEqual(job['attempt'],0)
