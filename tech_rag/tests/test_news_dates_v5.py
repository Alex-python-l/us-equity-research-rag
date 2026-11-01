from copy import deepcopy
from pathlib import Path
import hashlib,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class PublisherDateTests(unittest.TestCase):
    def reparse(self,html,**changes):
        from integration.news_dates import reverify_header_date
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'source.html';path.write_text(html,encoding='utf-8')
            record={'body_acquired':True,'body_complete':True,'raw_path':str(path),
                    'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'title':'Event','unknowns':[]}
            record.update(changes)
            return reverify_header_date(record,'2026-10-19T03:37:09+00:00','2026-09-19T03:37:09+00:00')

    def test_visible_publisher_date_is_conservative_interval_not_invented_instant(self):
        r=self.reparse('<ul class="post-info"><li><span class="date">October 16, 2026</span></li></ul>')
        self.assertTrue(r['eligible']);self.assertIsNone(r['actual_public_at'])
        self.assertEqual(r['publication_interval']['latest_exclusive'],'2026-10-17T12:00:00+00:00')
        self.assertFalse(r['historical_eligible']);self.assertTrue(r['exact_publication_time_unknown'])

    def test_conflicting_exact_clocks_stay_unknown_but_safe_whole_date_before_cutoff(self):
        r=self.reparse('<div class="wp-block-post-date"><time datetime="2026-10-08T12:24:41-05:00">October 8, 2026</time></div>',modified_at='2026-10-08T12:24:42+00:00')
        self.assertTrue(r['source_clock_chronology_conflict']);self.assertIsNone(r['actual_public_at'])
        self.assertTrue(r['eligible']);self.assertEqual(len(r['observed_original_time_metadata']),2)

    def test_date_interval_crossing_cutoff_or_future_revision_is_not_usable(self):
        self.assertFalse(self.reparse('<ul class="post-info"><span class="date">October 19, 2026</span></ul>')['eligible'])
        r=self.reparse('<ul class="post-info"><span class="date">October 16, 2026</span></ul>',modified_at='2026-10-20T00:00:00+00:00',eligible=False)
        self.assertFalse(r['eligible'])

    def test_news_table_encoding_is_lossless_including_headings_conditions_and_units(self):
        from integration.context_codec import pack_context,unpack_context
        view={'news_evidence':{'N1':{'text':'If qualified, capacity is 30 MW, not orders.','heading_path':['Data Center'],'char_start':0},
                               'N2':{'text':'No customer allocation is disclosed.','heading_path':['Data Center'],'char_start':41}}}
        compressed=pack_context(view)
        self.assertIn('news_evidence_table',compressed)
        self.assertEqual(unpack_context(compressed),view)

    def test_optional_ID_prefix_and_version_codes_reconstruct_full_citation_identity(self):
        from integration.context_codec import pack_context,unpack_context
        v='NEWS_0123456789abcdef01234567'
        view={'news_evidence':{'N_0123456789abcdef01234567_0001':{'text':'Not an order.','version_id':v},
                               'N_0123456789abcdef01234567_0002':{'text':'If qualified, 30 MW.','version_id':v}}}
        packed=pack_context(view,compact_ids=True)
        self.assertIn('id_prefix',packed['news_evidence_table'])
        self.assertIn('value_codebooks',packed['news_evidence_table'])
        self.assertEqual(unpack_context(packed),view)


if __name__=='__main__':unittest.main()
