"""SQLite monitor drafts, immutable confirmed versions, and leased execution jobs.

Constructing a store never starts a worker. All timestamps are persisted in UTC.
The caller owns network/model execution and checks dependencies again on resume.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from monitor_schedule import KINDS, next_run, preview_schedule, utc_datetime, validate_config


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _id(prefix):
    return f'{prefix}_{uuid.uuid4().hex}'


def _changes(before, after, prefix=''):
    rows = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            rows.extend(_changes(before.get(key), after.get(key), f'{prefix}.{key}'.strip('.')))
    elif before != after:
        rows.append({'field': prefix, 'before': before, 'after': after})
    return rows


def dependency_blockers(dependencies, config):
    if not isinstance(dependencies, dict):
        return ['dependencies must be an object']
    required = ['calendar', 'news']
    if config['budgets']['llm_calls_per_day'] > 0:
        required.append('agent')
    blockers = []
    for name in required:
        entry = dependencies.get(name)
        if not isinstance(entry, dict) or entry.get('ready') is not True:
            blockers.append(f'{name}: {entry.get("reason", "not ready") if isinstance(entry, dict) else "not ready"}')
    return blockers


class MonitorStore:
    LEASE_SECONDS = 900
    MAX_ATTEMPTS = 3

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS monitor_plans (
                    plan_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, market TEXT NOT NULL,
                    status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                    config TEXT NOT NULL, guide_id TEXT NOT NULL, watermark TEXT,
                    next_due TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, UNIQUE(ticker, market));
                CREATE TABLE IF NOT EXISTS monitor_drafts (
                    draft_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, config TEXT NOT NULL,
                    guide_id TEXT NOT NULL, base_version INTEGER NOT NULL,
                    status TEXT NOT NULL, preview TEXT, confirmation_hash TEXT,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS monitor_versions (
                    plan_id TEXT NOT NULL, version INTEGER NOT NULL, config TEXT NOT NULL,
                    guide_id TEXT NOT NULL, confirmed_at TEXT NOT NULL,
                    confirmation_hash TEXT NOT NULL, PRIMARY KEY(plan_id, version));
                CREATE TABLE IF NOT EXISTS monitor_jobs (
                    job_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, kind TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL, due_at TEXT NOT NULL, attempt INTEGER NOT NULL,
                    status TEXT NOT NULL, config TEXT NOT NULL, guide_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(plan_id, kind, scheduled_at, payload));
                CREATE TABLE IF NOT EXISTS monitor_runs (
                    run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                    kind TEXT NOT NULL, scheduled_at TEXT NOT NULL, attempt INTEGER NOT NULL,
                    status TEXT NOT NULL, started_at TEXT NOT NULL, lease_until TEXT NOT NULL,
                    finished_at TEXT, result TEXT, error TEXT, config TEXT NOT NULL,
                    guide_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    watermark TEXT);
                CREATE INDEX IF NOT EXISTS monitor_jobs_due ON monitor_jobs(status, due_at);
                CREATE INDEX IF NOT EXISTS monitor_runs_status ON monitor_runs(status, lease_until);
            ''')

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=20000')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _plan(row):
        if row is None:
            raise ValueError('plan not found')
        result = dict(row)
        result['config'] = json.loads(result['config'])
        result['next_due'] = json.loads(result['next_due'])
        return result

    @staticmethod
    def _draft(row):
        if row is None:
            raise ValueError('draft not found')
        result = dict(row)
        result['config'] = json.loads(result['config'])
        if result.get('preview'):
            result['preview'] = json.loads(result['preview'])
        return result

    def get_plan(self, plan_id):
        with self._db() as db:
            return self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan_id,)).fetchone())

    def get_draft(self, draft_id):
        with self._db() as db:
            return self._draft(db.execute('SELECT * FROM monitor_drafts WHERE draft_id=?', (draft_id,)).fetchone())

    def save_draft(self, config, guide_id, plan_id=None):
        config = validate_config(config)
        if not isinstance(guide_id, str) or not guide_id.strip():
            raise ValueError('guide_id is required')
        created = utc_datetime().isoformat()
        with self._db(write=True) as db:
            if plan_id:
                plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan_id,)).fetchone())
                if (plan['ticker'], plan['market']) != (config['ticker'], config['market']):
                    raise ValueError('a plan cannot change ticker or market')
            else:
                row = db.execute('SELECT * FROM monitor_plans WHERE ticker=? AND market=?', (config['ticker'], config['market'])).fetchone()
                if row:
                    plan = self._plan(row)
                else:
                    plan_id = _id('plan')
                    db.execute('INSERT INTO monitor_plans(plan_id,ticker,market,status,config,guide_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (plan_id, config['ticker'], config['market'], 'draft', _json(config), guide_id, created, created))
                    plan = {'plan_id': plan_id, 'version': 0}
            draft_id = _id('draft')
            db.execute('INSERT INTO monitor_drafts(draft_id,plan_id,config,guide_id,base_version,status,created_at) VALUES(?,?,?,?,?,?,?)',
                       (draft_id, plan['plan_id'], _json(config), guide_id, plan['version'], 'draft', created))
        return self.get_draft(draft_id)

    def preview(self, draft_id, dependencies, now=None):
        now = utc_datetime(now)
        with self._db(write=True) as db:
            draft = self._draft(db.execute('SELECT * FROM monitor_drafts WHERE draft_id=?', (draft_id,)).fetchone())
            plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (draft['plan_id'],)).fetchone())
            schedule = preview_schedule(draft['config'], now=now)
            blockers = dependency_blockers(dependencies, draft['config']) + schedule['errors']
            if not schedule['next_runs'] and not schedule['errors']:
                blockers.append('lifecycle has no future executions')
            if plan['version'] != draft['base_version'] and draft['status'] != 'confirmed':
                blockers.append('draft is based on an obsolete plan version')
            changes = _changes(plan['config'], draft['config']) if plan['version'] else []
            if plan['version'] and plan['guide_id'] != draft['guide_id']:
                changes.append({'field': 'guide_id', 'before': plan['guide_id'], 'after': draft['guide_id']})
            result = {'draft_id': draft_id, 'plan_id': draft['plan_id'], 'config': draft['config'],
                      'guide_id': draft['guide_id'], 'topics': [], 'next_runs': schedule['next_runs'],
                      'calendar': schedule['calendar'], 'dependencies': deepcopy(dependencies),
                      'blockers': blockers, 'changes': changes, 'base_version': draft['base_version'],
                      'previewed_at': now.isoformat()}
            cursor=now;end=now+timedelta(days=1);rounds=0
            if not schedule['errors']:
                from monitor_schedule import _calendar,_next
                calendar,_=_calendar(draft['config'],max(now,utc_datetime(draft['config']['lifecycle']['start_at'])))
                while rounds<1441:
                    point=_next(draft['config'],'search',cursor,calendar)
                    if not point or point>=end:break
                    rounds+=1;cursor=point
            query_count=min(2,len(draft['config'].get('topic_ids',[])) or 2)
            ceiling=rounds*query_count*(10+3*4)
            result['capacity']=dict(search_rounds_next_24h=rounds,request_upper_bound_without_shared_cache=ceiling,
                daily_request_cap=draft['config']['budgets']['requests_per_day'],
                full_worst_case_coverage=ceiling<=draft['config']['budgets']['requests_per_day'],
                note='Upper bound only, not expected usage. Requests are charged before actual transport; budget can defer later searches.')
            digest = hashlib.sha256(_json(result).encode('utf-8')).hexdigest()
            result['confirmation_hash'] = digest
            db.execute('UPDATE monitor_drafts SET preview=?,confirmation_hash=? WHERE draft_id=?', (_json(result), digest, draft_id))
            return result

    def confirm(self, draft_id, confirmation_hash, dependencies, now=None):
        now = utc_datetime(now)
        with self._db(write=True) as db:
            draft = self._draft(db.execute('SELECT * FROM monitor_drafts WHERE draft_id=?', (draft_id,)).fetchone())
            if not confirmation_hash or confirmation_hash != draft['confirmation_hash'] or not draft['preview']:
                raise ValueError('preview confirmation hash is missing or stale')
            plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (draft['plan_id'],)).fetchone())
            blockers = dependency_blockers(dependencies, draft['config'])
            schedule = preview_schedule(draft['config'], now=now)
            blockers.extend(schedule['errors'])
            if draft['preview']['blockers']:
                blockers.extend(draft['preview']['blockers'])
            if blockers:
                raise ValueError('cannot confirm: ' + '; '.join(dict.fromkeys(blockers)))
            if draft['status'] == 'confirmed':
                return plan
            if plan['status'] == 'ended':
                raise ValueError('ended plan cannot be reactivated; create a fresh research decision')
            same = plan['version'] and plan['config'] == draft['config'] and plan['guide_id'] == draft['guide_id']
            if plan['version'] != draft['base_version'] and not same:
                raise ValueError('draft is based on an obsolete plan version')
            if not same:
                due = {kind: next_run(draft['config'], kind, now) for kind in KINDS}
                if not any(due.values()):
                    raise ValueError('lifecycle has no future executions')
                version = plan['version'] + 1
                db.execute('INSERT INTO monitor_versions VALUES(?,?,?,?,?,?)',
                           (plan['plan_id'], version, _json(draft['config']), draft['guide_id'], now.isoformat(), confirmation_hash))
                # A running job retains its immutable version; queued obsolete work is cancelled.
                db.execute("UPDATE monitor_jobs SET status='cancelled' WHERE plan_id=? AND status='pending'", (plan['plan_id'],))
                db.execute('UPDATE monitor_plans SET config=?,guide_id=?,version=?,status=?,next_due=?,updated_at=? WHERE plan_id=?',
                           (_json(draft['config']), draft['guide_id'], version, 'active', _json(due), now.isoformat(), plan['plan_id']))
            db.execute("UPDATE monitor_drafts SET status='confirmed' WHERE draft_id=?", (draft_id,))
            return self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan['plan_id'],)).fetchone())

    def action(self, plan_id, action):
        if action not in ('pause', 'resume', 'end'):
            raise ValueError('action must be pause, resume, or end')
        with self._db(write=True) as db:
            plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan_id,)).fetchone())
            if not plan['version']:
                raise ValueError('draft must be confirmed before lifecycle actions')
            if plan['status'] == 'ended' and action != 'end':
                raise ValueError('ended plan cannot resume')
            status = {'pause': 'paused', 'resume': 'active', 'end': 'ended'}[action]
            db.execute('UPDATE monitor_plans SET status=?,updated_at=? WHERE plan_id=?', (status, utc_datetime().isoformat(), plan_id))
            if action == 'end':
                db.execute("UPDATE monitor_jobs SET status='cancelled' WHERE plan_id=? AND status='pending'", (plan_id,))
            return self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan_id,)).fetchone())

    def list_plans(self):
        with self._db() as db:
            return [self._plan(row) for row in db.execute('SELECT * FROM monitor_plans ORDER BY created_at,plan_id')]

    def list_runs(self, plan_id=None):
        query = 'SELECT * FROM monitor_runs'
        args = ()
        if plan_id:
            query += ' WHERE plan_id=?'
            args = (plan_id,)
        with self._db() as db:
            rows = []
            for row in db.execute(query + ' ORDER BY started_at DESC,run_id', args):
                result = dict(row)
                for key in ('config', 'payload', 'result'):
                    result[key] = json.loads(result[key]) if result[key] is not None else None
                rows.append(result)
            return rows

    def enqueue_followup(self, plan_id, event_id, at_utc):
        at = utc_datetime(at_utc).isoformat()
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError('event_id required')
        with self._db(write=True) as db:
            plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (plan_id,)).fetchone())
            if not plan['version'] or plan['status'] == 'ended':
                raise ValueError('followup requires a confirmed unended plan')
            payload = _json({'event_id': event_id})
            existing = db.execute('SELECT job_id FROM monitor_jobs WHERE plan_id=? AND kind=? AND scheduled_at=? AND payload=?',
                                  (plan_id, 'event_followup', at, payload)).fetchone()
            if existing:
                return existing['job_id']
            job_id = _id('job')
            db.execute('INSERT INTO monitor_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (job_id, plan_id, 'event_followup', at, at, 0, 'pending', _json(plan['config']), plan['guide_id'], plan['version'], payload))
            return job_id

    def _recover(self, db, now):
        for run in db.execute("SELECT * FROM monitor_runs WHERE status='running' AND lease_until<=?", (now.isoformat(),)).fetchall():
            db.execute("UPDATE monitor_runs SET status='failed',finished_at=?,error='lease_expired' WHERE run_id=?", (now.isoformat(), run['run_id']))
            plan = db.execute('SELECT version,status FROM monitor_plans WHERE plan_id=?', (run['plan_id'],)).fetchone()
            obsolete = plan is None or plan['version'] != run['version'] or plan['status'] == 'ended'
            state = 'cancelled' if obsolete else ('pending' if run['attempt'] < self.MAX_ATTEMPTS else 'failed')
            db.execute('UPDATE monitor_jobs SET status=?,due_at=? WHERE job_id=?', (state, now.isoformat(), run['job_id']))

    def claim_due(self, now=None):
        now = utc_datetime(now)
        with self._db(write=True) as db:
            self._recover(db, now)
            if db.execute("SELECT 1 FROM monitor_runs WHERE status='running' LIMIT 1").fetchone():
                return None
            for row in db.execute("SELECT * FROM monitor_plans WHERE status='active'").fetchall():
                plan = self._plan(row)
                end = plan['config']['lifecycle']['end_at']
                if end and utc_datetime(end) <= now:
                    db.execute("UPDATE monitor_plans SET status='ended',updated_at=? WHERE plan_id=?", (now.isoformat(), plan['plan_id']))
                    db.execute("UPDATE monitor_jobs SET status='cancelled' WHERE plan_id=? AND status='pending'", (plan['plan_id'],))
                    continue
                changed = False
                for kind, at in plan['next_due'].items():
                    if not at or utc_datetime(at) > now:
                        continue
                    if db.execute("SELECT 1 FROM monitor_jobs WHERE plan_id=? AND kind=? AND status IN ('pending','running')", (plan['plan_id'], kind)).fetchone():
                        continue
                    job_id = _id('job')
                    db.execute('INSERT OR IGNORE INTO monitor_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (job_id, plan['plan_id'], kind, at, at, 0, 'pending', _json(plan['config']), plan['guide_id'], plan['version'], '{}'))
                    plan['next_due'][kind] = next_run(plan['config'], kind, now)
                    changed = True
                if changed:
                    db.execute('UPDATE monitor_plans SET next_due=? WHERE plan_id=?', (_json(plan['next_due']), plan['plan_id']))
            row = db.execute("SELECT j.* FROM monitor_jobs j JOIN monitor_plans p ON p.plan_id=j.plan_id WHERE j.status='pending' AND j.due_at<=? AND p.status='active' ORDER BY j.due_at,j.scheduled_at,j.job_id LIMIT 1", (now.isoformat(),)).fetchone()
            if row is None:
                return None
            job = dict(row)
            plan = self._plan(db.execute('SELECT * FROM monitor_plans WHERE plan_id=?', (job['plan_id'],)).fetchone())
            run_id, attempt = _id('run'), job['attempt'] + 1
            db.execute('INSERT INTO monitor_runs(run_id,job_id,plan_id,kind,scheduled_at,attempt,status,started_at,lease_until,config,guide_id,version,payload,watermark) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, job['job_id'], job['plan_id'], job['kind'], job['scheduled_at'], attempt, 'running', now.isoformat(),
                        (now + timedelta(seconds=self.LEASE_SECONDS)).isoformat(), job['config'], job['guide_id'], job['version'], job['payload'], plan['watermark']))
            db.execute("UPDATE monitor_jobs SET status='running',attempt=? WHERE job_id=?", (attempt, job['job_id']))
            return {'run_id': run_id, 'plan_id': job['plan_id'], 'kind': job['kind'],
                    'scheduled_at': job['scheduled_at'], 'attempt': attempt, 'config': json.loads(job['config']),
                    'guide_id': job['guide_id'], 'watermark': plan['watermark'], 'payload': json.loads(job['payload']),
                    'version': job['version']}

    def finish_run(self, run_id, status, result, watermark=None, error=None, now=None):
        now = utc_datetime(now)
        if status in ('success', 'succeeded'):
            status = 'success'
        elif status not in ('failed', 'skipped', 'blocked', 'deferred'):
            raise ValueError('unsupported run status')
        result_json = _json(result)
        with self._db(write=True) as db:
            run = db.execute('SELECT * FROM monitor_runs WHERE run_id=?', (run_id,)).fetchone()
            if run is None or run['status'] != 'running' or utc_datetime(run['lease_until']) <= now:
                raise ValueError('run lease is missing, expired, or already finished')
            db.execute('UPDATE monitor_runs SET status=?,finished_at=?,result=?,error=? WHERE run_id=?',
                       (status, now.isoformat(), result_json, str(error) if error is not None else None, run_id))
            plan = db.execute('SELECT version,status FROM monitor_plans WHERE plan_id=?', (run['plan_id'],)).fetchone()
            obsolete = plan is None or plan['version'] != run['version'] or plan['status'] == 'ended'
            if status=='deferred' and not obsolete:
                retry_at=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
                db.execute("UPDATE monitor_jobs SET status='pending',due_at=?,attempt=MAX(0,attempt-1) WHERE job_id=?",(retry_at.isoformat(),run['job_id']))
            elif status == 'failed' and run['attempt'] < self.MAX_ATTEMPTS and not obsolete:
                retry_at = now + timedelta(seconds=60 * (2 ** (run['attempt'] - 1)))
                db.execute("UPDATE monitor_jobs SET status='pending',due_at=? WHERE job_id=?", (retry_at.isoformat(), run['job_id']))
            else:
                db.execute('UPDATE monitor_jobs SET status=? WHERE job_id=?', (status, run['job_id']))
            if status == 'success' and run['kind'] == 'search' and watermark is not None:
                value = utc_datetime(watermark)
                if value > now:
                    raise ValueError('watermark cannot be in the future')
                current = db.execute('SELECT watermark FROM monitor_plans WHERE plan_id=?', (run['plan_id'],)).fetchone()['watermark']
                if current is None or value > utc_datetime(current):
                    db.execute('UPDATE monitor_plans SET watermark=? WHERE plan_id=?', (value.isoformat(), run['plan_id']))
            return {'run_id': run_id, 'status': status}

    def assert_owner(self,run_id,now=None):
        at=utc_datetime(now)
        with self._db() as db:
            row=db.execute("SELECT 1 FROM monitor_runs r JOIN monitor_plans p ON p.plan_id=r.plan_id WHERE r.run_id=? AND r.status='running' AND r.lease_until>? AND p.status='active' AND p.version=r.version",(run_id,at.isoformat())).fetchone()
        if not row:raise ValueError('Run lease or confirmed configuration is no longer owned')

    def renew_lease(self,run_id,now=None):
        at=utc_datetime(now)
        with self._db(write=True) as db:
            row=db.execute("SELECT 1 FROM monitor_runs r JOIN monitor_plans p ON p.plan_id=r.plan_id WHERE r.run_id=? AND r.status='running' AND r.lease_until>? AND p.status='active' AND p.version=r.version",(run_id,at.isoformat())).fetchone()
            if not row:raise ValueError('Cannot renew expired or obsolete run')
            db.execute('UPDATE monitor_runs SET lease_until=? WHERE run_id=?',((at+timedelta(seconds=self.LEASE_SECONDS)).isoformat(),run_id))
