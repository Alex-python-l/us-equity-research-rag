from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synthetic_kb import make_kb
from knowledge_versions import KnowledgeVersions,active_snapshot
from knowledge_maintenance import KnowledgeMaintenance,next_times,OfficialCollector

class KnowledgeTests(unittest.TestCase):
    def test_publish_is_atomic_and_old_snapshot_survives(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_kb(root);versions=KnowledgeVersions(root)
            old=active_snapshot(root);first=versions.publish()
            self.assertEqual(old.generation,'legacy');self.assertEqual(active_snapshot(root).generation,first['generation'])
            def interrupted():raise RuntimeError('interrupted before pointer switch')
            with self.assertRaises(RuntimeError):versions.publish(check_owner=interrupted)
            self.assertEqual(active_snapshot(root).generation,first['generation'])
            (root/'raw/a.html').write_text('tampered')
            with self.assertRaises(ValueError):versions.audit()
    def test_unconfirmed_maintenance_never_executes_and_confirm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_kb(root);jobs=KnowledgeMaintenance(root)
            self.assertIsNone(jobs.tick());preview=jobs.preview({})
            self.assertEqual(preview['status'],'draft');self.assertIsNone(jobs.tick())
            first=jobs.confirm(preview['confirmation_hash']);second=jobs.confirm(preview['confirmation_hash'])
            self.assertEqual(first['status'],'active');self.assertEqual(first['next_times'],second['next_times'])
            jobs.action('pause');self.assertIsNone(jobs.tick())
    def test_schedule_is_real_calendar_and_named_sunday(self):
        out=next_times({},'2026-10-30T19:00:00Z')
        self.assertEqual(out['ingest'][0],'2026-10-30T21:30:00+00:00')
        self.assertEqual(out['audit'][0],'2026-11-01T01:00:00+00:00')

    def test_incremental_publish_is_searchable_without_promoting_graph(self):
        import hashlib,json
        from research import ResearchEngine
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=make_kb(root);versions=KnowledgeVersions(root)
            old=active_snapshot(root);old_engine=ResearchEngine(snapshot=old,index_path=root/'old.sqlite3')
            raw=b'<p>Synthetic new GPU supply disclosure.</p>';(root/'raw/new.html').write_bytes(raw)
            block=dict(source_id='new',pdf_page=None,paragraph=1,text='Synthetic new GPU supply disclosure.')
            (root/'text/new.jsonl').write_text(json.dumps(block)+'\n',encoding='utf-8')
            new={**source,'source_id':'new','local_path':'raw/new.html','text_path':'text/new.jsonl',
                 'sha256':hashlib.sha256(raw).hexdigest()}
            first=versions.publish([(new,[block])]);again=versions.publish([(new,[block])])
            self.assertEqual(first['added_sources'],['new']);self.assertEqual(again['added_sources'],[])
            self.assertEqual(len(old_engine.chunks),1)
            current=ResearchEngine(snapshot=active_snapshot(root),index_path=root/'new.sqlite3')
            result=current.build('NVDA','GPU supply','current',{'use_dense':False},mode='keyword')
            self.assertIn('new',{v['source_id'] for v in result['citations'].values()})
            self.assertEqual(current.tools.relations,[])

    def test_official_collector_has_incremental_accession_identity(self):
        import json,os
        from unittest.mock import patch
        from common import now
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_kb(root);stamp=now();calls=[]
            def request(url):
                calls.append(url)
                if '/submissions/' in url:
                    return json.dumps(dict(filings=dict(recent=dict(form=['8-K'],acceptanceDateTime=[stamp],accessionNumber=['0001045810-26-999999'],primaryDocument=['report.htm'],reportDate=['2026-10-31'])))).encode()
                if 'Archives/' in url:return b'<main><p>Synthetic new supplier capacity disclosure.</p></main>'
                if 'example.test' in url:return b'<p>Synthetic supplier disclosure.</p>'
                return b'<main></main>'
            collector=OfficialCollector(root,request=request)
            with patch('integration.source_config.configure_sources'),patch.dict(os.environ,{'SEC_EDGAR_USER_AGENT':'Synthetic contact@example.test'}):
                collected=collector.collect({'tickers':['NVDA']})
                self.assertEqual(len(collected['documents']),1);self.assertEqual(collected['errors'],[])
                KnowledgeVersions(root).publish(collected['documents'])
                second=collector.collect({'tickers':['NVDA']})
            self.assertEqual(second['documents'],[])
            self.assertEqual(sum('Archives/' in url for url in calls),1)

    def test_publication_lock_and_confirmed_audit_recover_without_browser(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);make_kb(root);versions=KnowledgeVersions(root)
            with versions.publication_lock():
                with self.assertRaises(RuntimeError):versions.publish()
            self.assertEqual(versions.audit()['status'],'verified')
            jobs=KnowledgeMaintenance(root);preview=jobs.preview({});jobs.confirm(preview['confirmation_hash'])
            with jobs.db() as db:
                row=db.execute('SELECT next_times FROM settings WHERE id=1').fetchone();times=json.loads(row['next_times'])
                times['audit'][0]='2020-01-01T00:00:00Z';db.execute('UPDATE settings SET next_times=? WHERE id=1',(json.dumps(times),))
            restored=KnowledgeMaintenance(root)
            result=restored.tick();self.assertEqual(result['status'],'verified')
            self.assertEqual(restored.status()['runs'][0]['status'],'completed')
