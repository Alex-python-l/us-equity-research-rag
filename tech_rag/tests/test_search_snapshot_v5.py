from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class HostSearchTests(unittest.TestCase):
    def test_source_aware_selection_never_uses_results_beyond_three_or_retries_known_failure(self):
        from integration.search_snapshots import source_aware_candidates
        snapshot={'items':[{'url':'https://pay.example/a'},{'url':'https://public.example/a'},
                            {'url':'https://exchange.example/old.pdf'}]}
        cache={'https://pay.example/earlier':{'status':'access_restricted','eligible':False}}
        got=source_aware_candidates(snapshot,cache)
        self.assertEqual([x['url'] for x in got['items']],['https://public.example/a'])
        self.assertEqual(len(got['excluded_candidates']),2)
        self.assertEqual(snapshot['items'][0]['url'],'https://pay.example/a')

    def test_verified_body_cache_rechecks_hash_and_does_not_retry_failures(self):
        from integration.search_snapshots import CachedArticleProvider
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);raw=folder/'source.html';raw.write_text('<article>News body</article>',encoding='utf-8')
            article={'as_of':'2026-10-19T03:37:09+00:00','body_acquired':True,'source_verified':True,
                     'eligible':True,'raw_path':str(raw),'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
                     'body_text':'News body','body_sha256':hashlib.sha256(b'News body').hexdigest(),
                     'paragraphs':[{'text':'News body','char_start':0,'char_end':9}]}
            path=folder/'body_cache.json';path.write_text(json.dumps({'https://example.com/a':article,'https://example.com/fail':{'as_of':article['as_of'],'status':'access_restricted','eligible':False,'body_acquired':False}}),encoding='utf-8')
            provider=CachedArticleProvider(path)
            got=provider({'url':'https://example.com/a'},article['as_of'],archive_dir=folder)
            self.assertTrue(got['eligible']);self.assertTrue(got['body_cache_reused'])
            raw.write_text('tampered',encoding='utf-8')
            self.assertFalse(provider({'url':'https://example.com/a'},article['as_of'],archive_dir=folder)['eligible'])
            self.assertEqual(provider({'url':'https://example.com/fail'},article['as_of'],archive_dir=folder)['status'],'access_restricted')

    def test_engine_results_are_not_ordinary_llm_answers_or_verified_bodies(self):
        from integration.search_snapshots import normalize_host_response
        raw={'provider':'Codex hosted web search','query':'HBM capacity','query_hash':'x',
             'as_of':'2026-10-19T03:37:09+00:00','time_window':{},'actual_tool':'web__run',
             'raw':'Article one (https://example.com/one)\nReference Published: 2 days ago; text\n--------------------------------------------------------------------------------\nArticle two (https://example.com/two)\nReference Published: last month; text'}
        got=normalize_host_response(raw,max_results=1)
        self.assertEqual(len(got['items']),1)
        self.assertFalse(got['items'][0]['source_verified'])
        self.assertFalse(got['items'][0]['body_acquired'])
        self.assertEqual(got['raw_engine_result_count'],2)
        self.assertIsNone(got['provider_token_usage'])

    def test_replay_cannot_substitute_another_query_or_cutoff_and_never_contacts_network(self):
        from integration.search_snapshots import SnapshotSearchProvider
        row={'query':'HBM capacity','query_hash':'x','as_of':'2026-10-19T03:37:09+00:00',
             'time_window':{'start':'2026-09-19','end':'2026-10-19'},'raw':'Article (https://example.com)\nRef',
             'actual_tool':'web__run'}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x_raw.json';p.write_text(json.dumps(row),encoding='utf-8')
            source=SnapshotSearchProvider(td)
            task={'query_hash':'x','query_en':'HBM capacity','time_window':row['time_window']}
            self.assertEqual(source(task,row['as_of'])['status'],'candidates')
            self.assertTrue(source(task,row['as_of'])['reused_snapshot'])
            self.assertEqual(source({**task,'query_en':'Other'},row['as_of'])['status'],'provider_unavailable')
            self.assertEqual(source(task,'2025-01-01T00:00:00+00:00')['status'],'provider_unavailable')
            self.assertEqual(source(task,row['as_of'],historical=True)['status'],'unsupported_historical')


if __name__=='__main__':unittest.main()
