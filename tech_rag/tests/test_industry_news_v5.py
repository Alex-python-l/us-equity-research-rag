"""Industry discovery and real HTML provenance; all network boundaries are faked."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AS_OF = "2026-10-18T12:00:00+00:00"
QUERY = {"task_id": "industry-1", "query_en": "advanced packaging capacity demand",
         "query_zh": "先进封装产能需求", "time_window": {
             "start": "2026-10-01T00:00:00+00:00", "end": AS_OF}}
PROSE = ("The publisher reports advanced packaging capacity changes. "
         "The figures below are quarterly segment revenue in USD millions, "
         "rather than total company revenue or an annual production forecast.")
HTML = ("""<html><head><title>Original publisher title</title>
<meta property="og:site_name" content="Original Publisher">
<meta property="og:type" content="article">
<script type="application/ld+json">{"@context":"https://schema.org",
"@type":"NewsArticle","headline":"Publisher headline",
"datePublished":"2026-10-17T09:00:00Z","dateModified":"2026-10-18T08:00:00Z",
"publisher":{"@type":"Organization","name":"Original Publisher"}}</script>
</head><body><nav><p>Navigation must not enter evidence.</p></nav><article>
<h1>Publisher headline</h1><p>""" + PROSE + """</p>
<h2>Quarterly segment revenue (USD millions)</h2>
<table><caption>Q3 2026 segment revenue; unaudited</caption>
<tr><th>Segment</th><th>Revenue (USD millions)</th></tr>
<tr><td>Packaging</td><td>89.0</td></tr></table>
<p>Table excludes intersegment sales and uses the same reporting period.</p>
</article><footer><p>Footer must not enter evidence.</p></footer></body></html>""").encode()


class FakeResponse:
    """Only the external HTTP response is replaced; parsing/storage stay real."""
    def __init__(self, content=HTML, status=200, headers=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.content = content
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


def public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class IndustryNewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("integration.industry_news"):
            from integration import industry_news
            cls.module = industry_news
        else:
            cls.module = None

    def setUp(self):
        self.assertIsNotNone(self.module, "industry news module is not implemented")

    def test_generic_industry_query_uses_search_without_ticker_resolution(self):
        articles = [{"title": "Industry capacity", "link": "https://publisher.example/news",
                     "publisher": "Publisher", "providerPublishTime": 1792227600}]
        with patch("yfinance.Search", return_value=SimpleNamespace(news=articles)) as search:
            result = self.module.search_yahoo(QUERY, AS_OF)
        self.assertEqual(search.call_args.args[0], "advanced packaging capacity demand")
        self.assertEqual(search.call_args.kwargs["news_count"], 3)
        self.assertEqual(search.call_args.kwargs["max_results"], 0)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["items"][0]["publisher"], "Publisher")
        self.assertFalse(result["items"][0]["body_acquired"])
        self.assertFalse(result["items"][0]["source_verified"])
        self.assertEqual(result["query"], QUERY["query_en"])
        self.assertGreaterEqual(result["seconds"], 0)

    def test_search_filters_dates_deduplicates_and_keeps_unknown_date_as_candidate(self):
        articles = [
            {"content": {"title": "Valid", "pubDate": "2026-10-17T09:00:00Z",
                         "provider": {"displayName": "Publisher"},
                         "canonicalUrl": {"url": "https://publisher.example/valid"}}},
            {"title": "Future", "pubDate": "2026-10-19T00:00:00Z", "link": "https://publisher.example/future"},
            {"title": "Old", "pubDate": "2020-01-01T00:00:00Z", "link": "https://publisher.example/old"},
            {"title": "Unknown date", "link": "https://publisher.example/unknown", "summary": "This is only a snippet."},
            {"title": "Unsafe", "pubDate": "2026-10-17T09:00:00Z", "link": "http://127.0.0.1/private"},
        ]
        with patch("yfinance.Search", return_value=SimpleNamespace(news=articles + [articles[0]])):
            result = self.module.search_yahoo(QUERY, AS_OF, max_results=5)
        self.assertEqual({i["title"] for i in result["items"]}, {"Valid", "Unknown date"})
        unknown = next(i for i in result["items"] if i["title"] == "Unknown date")
        self.assertIsNone(unknown["pubtime"])
        self.assertFalse(unknown["eligible"])
        self.assertNotIn("paragraphs", unknown)

    def test_historical_search_has_no_provider_call_or_local_fallback(self):
        with patch("yfinance.Search", side_effect=AssertionError("must not execute")):
            result = self.module.search_yahoo(QUERY, AS_OF, historical=True)
        self.assertEqual(result["status"], "unsupported_historical")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["calls"], 0)
        self.assertFalse(result["real_network_executed"])

    def test_search_provider_failure_is_explicit_and_does_not_expose_exception_detail(self):
        with patch("yfinance.Search", side_effect=TimeoutError("credential-like private detail")):
            result = self.module.search_yahoo(QUERY, AS_OF)
        self.assertEqual(result["status"], "provider_unavailable")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["calls"], 1)
        self.assertNotIn("credential-like", json.dumps(result))

    def test_search_cache_is_an_audit_record_never_a_sample_news_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            with patch("yfinance.Search", return_value=SimpleNamespace(news=[])):
                result = self.module.search_yahoo(QUERY, AS_OF, cache_dir=cache)
            self.assertEqual(result["status"], "no_candidates")
            self.assertEqual(result["items"], [])
            saved = json.loads(Path(result["cache_path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["query"], QUERY["query_en"])
            self.assertEqual(saved["items"], [])

    def test_search_cache_write_failure_is_reported_without_fabricating_candidates(self):
        with patch("yfinance.Search", return_value=SimpleNamespace(news=[])), \
             patch.object(Path, "mkdir", side_effect=PermissionError("private path detail")):
            result = self.module.search_yahoo(QUERY, AS_OF, cache_dir=Path("unwritable"))
        self.assertEqual(result["items"], [])
        self.assertIn({"code": "cache_write_unavailable"}, result["errors"])
        self.assertNotIn("private path detail", json.dumps(result))


class ArticleBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("integration.industry_news"):
            from integration import industry_news, news_body
            cls.module, cls.body = industry_news, news_body
        else:
            cls.module, cls.body = None, None

    def setUp(self):
        self.assertIsNotNone(self.module, "article body acquisition is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Path(self.tmp.name)
        self.item = {"candidate_id": "NC_original", "title": "Search title",
                     "url": "https://publisher.example/news", "publisher": "Search provider",
                     "pubtime": "2026-10-01T00:00:00Z", "summary": "Never use this as a body."}

    def fetch(self, response=None, **kwargs):
        response = response or FakeResponse()
        with patch("socket.getaddrinfo", side_effect=public_dns), \
             patch("requests.Session.get", return_value=response) as get:
            result = self.module.fetch_article(self.item, AS_OF, archive_dir=self.archive, **kwargs)
        return result, get, response

    def test_real_dom_body_preserves_source_dates_headings_table_and_archive(self):
        result, get, response = self.fetch()
        self.assertTrue(result["body_acquired"])
        self.assertTrue(result["source_verified"])
        self.assertTrue(result["eligible"])
        self.assertEqual(result["published_at"], "2026-10-17T09:00:00+00:00")
        self.assertEqual(result["modified_at"], "2026-10-18T08:00:00+00:00")
        self.assertEqual(result["title"], "Publisher headline")
        self.assertEqual(result["publisher"], "Original Publisher")
        self.assertIsNone(result["event_time"])
        self.assertNotIn("Never use this", result["body_text"])
        self.assertNotIn("Navigation must", result["body_text"])
        self.assertNotIn("Footer must", result["body_text"])
        table = next(p for p in result["paragraphs"] if p["kind"] == "table")
        self.assertIn("Revenue (USD millions)", table["text"])
        self.assertIn("Q3 2026 segment revenue; unaudited", table["text"])
        self.assertEqual(table["heading_path"], ["Publisher headline", "Quarterly segment revenue (USD millions)"])
        self.assertIn("Packaging | 89.0", table["text"])
        self.assertEqual(Path(result["raw_path"]).read_bytes(), HTML)
        self.assertEqual(result["raw_sha256"], hashlib.sha256(HTML).hexdigest())
        self.assertEqual(result["body_sha256"], hashlib.sha256(result["body_text"].encode()).hexdigest())
        archived = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
        self.assertEqual(archived["paragraphs"], result["paragraphs"])
        for p in result["paragraphs"]:
            self.assertTrue(p["citation_id"].startswith("N_"))
            self.assertEqual(result["body_text"][p["char_start"]:p["char_end"]], p["text"])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertTrue(response.closed)

    def test_current_collection_after_frozen_cutoff_never_claims_historical_version(self):
        result, _, _ = self.fetch()
        self.assertTrue(result["retrieved_after_cutoff"])
        self.assertEqual(result["version_scope"], "current_as_collected")
        self.assertFalse(result["historical_eligible"])
        self.assertFalse(result["as_of_eligible"])
        self.assertFalse(result["version_as_of_verified"])

    def test_repeated_identical_response_preserves_version_citations_and_first_retrieval(self):
        first, _, _ = self.fetch()
        second, _, _ = self.fetch()
        self.assertEqual(first["version_id"], second["version_id"])
        self.assertEqual(first["first_retrieved_at"], second["first_retrieved_at"])
        self.assertEqual(first["paragraphs"], second["paragraphs"])

    def test_historical_fetch_never_resolves_or_requests_live_page(self):
        with patch("requests.Session.get", side_effect=AssertionError("live GET forbidden")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("live DNS forbidden")):
            result = self.module.fetch_article(self.item, AS_OF, historical=True, archive_dir=self.archive)
        self.assertEqual(result["calls"], 0)
        self.assertFalse(result["real_network_executed"])
        self.assertFalse(result["body_acquired"])
        self.assertFalse(result["eligible"])

    def test_search_metadata_and_http_last_modified_cannot_replace_original_publication_time(self):
        no_dates = HTML.replace(b'"datePublished":"2026-10-17T09:00:00Z",', b"")
        result, _, _ = self.fetch(FakeResponse(no_dates, headers={"Last-Modified": "Fri, 17 Oct 2026 09:00:00 GMT"}))
        self.assertTrue(result["body_acquired"])
        self.assertIsNone(result["published_at"])
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["eligible"])
        self.assertIn("publication_time_unknown", result["unknowns"])

    def test_standard_article_meta_dates_are_accepted_but_date_only_is_ambiguous(self):
        html = HTML.replace(b'"datePublished":"2026-10-17T09:00:00Z",', b"")
        html = html.replace(b"</head>", b'<meta property="article:published_time" content="2026-10-17T10:00:00+01:00"></head>')
        result, _, _ = self.fetch(FakeResponse(html))
        self.assertEqual(result["published_at"], "2026-10-17T09:00:00+00:00")
        html = html.replace(b"2026-10-17T10:00:00+01:00", b"2026-10-17")
        result, _, _ = self.fetch(FakeResponse(html))
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["published_at"])

    def test_conflicting_original_publication_metadata_is_not_source_verified(self):
        html = HTML.replace(b"</head>", b'<meta property="article:published_time" content="2026-10-16T09:00:00Z"></head>')
        result, _, _ = self.fetch(FakeResponse(html))
        self.assertFalse(result["source_verified"])
        self.assertFalse(result["eligible"])
        self.assertIn("conflicting_publication_times", result["unknowns"])

    def test_future_publication_or_reversed_revision_is_ineligible(self):
        future = HTML.replace(b"2026-10-17T09:00:00Z", b"2026-10-20T09:00:00Z")
        result, _, _ = self.fetch(FakeResponse(future))
        self.assertFalse(result["eligible"])
        self.assertIn("publication_after_cutoff", result["unknowns"])
        reversed_revision = HTML.replace(b"2026-10-18T08:00:00Z", b"2026-10-16T08:00:00Z")
        result, _, _ = self.fetch(FakeResponse(reversed_revision))
        self.assertFalse(result["eligible"])
        self.assertIn("modification_before_publication", result["unknowns"])

    def test_revision_after_frozen_cutoff_is_retained_only_as_ineligible_candidate(self):
        html = HTML.replace(b"2026-10-18T08:00:00Z", b"2026-10-19T08:00:00Z")
        result, _, _ = self.fetch(FakeResponse(html))
        self.assertTrue(result["body_acquired"])
        self.assertEqual(result["modified_at"], "2026-10-19T08:00:00+00:00")
        self.assertIn("modified_after_cutoff", result["unknowns"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "candidate_body")

    def test_paywall_or_access_denial_is_candidate_without_body_and_without_retry(self):
        paywall = HTML.replace(b'"@type":"NewsArticle",', b'"@type":"NewsArticle","isAccessibleForFree":false,')
        result, _, _ = self.fetch(FakeResponse(paywall))
        self.assertFalse(result["body_acquired"])
        self.assertEqual(result["paragraphs"], [])
        self.assertEqual(result["status"], "access_restricted")
        self.assertIsNone(result["raw_path"])
        for status in (401, 403, 429):
            result, get, _ = self.fetch(FakeResponse(status=status))
            self.assertEqual(get.call_count, 1)
            self.assertFalse(result["eligible"])
            self.assertFalse(result["body_acquired"])

    def test_pdf_and_snippet_only_page_never_become_news_body(self):
        result, _, _ = self.fetch(FakeResponse(b"%PDF-1.7", headers={"Content-Type": "application/pdf"}))
        self.assertEqual(result["status"], "unsupported_content_type")
        self.assertFalse(result["body_acquired"])
        result, _, _ = self.fetch(FakeResponse(b"<html><body><p>A search snippet.</p></body></html>"))
        self.assertFalse(result["body_acquired"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["paragraphs"], [])

    def test_body_char_limit_does_not_cut_table_or_hide_incompleteness(self):
        result, _, _ = self.fetch(max_chars=300)
        self.assertLessEqual(len(result["body_text"]), 300)
        self.assertFalse(result["eligible"])
        self.assertFalse(result["body_complete"])
        self.assertIn("body_char_limit", result["unknowns"])
        for block in result["paragraphs"]:
            if block["kind"] == "table":
                self.assertIn("Packaging | 89.0", block["text"])

    def test_response_byte_limit_stops_stream_without_body(self):
        response = FakeResponse(HTML + b" " * 1000)
        with patch.object(self.body, "MAX_RESPONSE_BYTES", 200):
            result, _, response = self.fetch(response)
        self.assertEqual(result["status"], "response_too_large")
        self.assertFalse(result["body_acquired"])
        self.assertTrue(response.closed)

    def test_timeout_is_single_attempt_with_no_exception_or_snippet_leak(self):
        import requests
        with patch("socket.getaddrinfo", side_effect=public_dns), \
             patch("requests.Session.get", side_effect=requests.Timeout("private server detail")) as get:
            result = self.module.fetch_article(self.item, AS_OF, archive_dir=self.archive)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["status"], "fetch_unavailable")
        self.assertFalse(result["body_acquired"])
        self.assertNotIn("private server detail", json.dumps(result))

    def test_ssrf_rejects_local_literal_dns_alias_and_redirect_before_contact(self):
        bad_urls = ["http://127.0.0.1/admin", "http://[::1]/", "http://169.254.169.254/latest",
                    "http://10.2.3.4/", "file:///etc/passwd", "http://user:pass@publisher.example/",
                    "http://localhost/", "http://2130706433/", "http://[::ffff:127.0.0.1]/"]
        for url in bad_urls:
            with self.subTest(url=url), patch("socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]), \
                 patch("requests.Session.get", side_effect=AssertionError("SSRF GET")):
                result = self.module.fetch_article({**self.item, "url": url}, AS_OF, archive_dir=self.archive)
            self.assertEqual(result["status"], "unsafe_url")
            self.assertEqual(result["calls"], 0)
        with patch("socket.getaddrinfo", side_effect=public_dns), \
             patch("requests.Session.get", return_value=FakeResponse(status=302, headers={"Location": "http://127.0.0.1/private"})) as get:
            result = self.module.fetch_article(self.item, AS_OF, archive_dir=self.archive)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["status"], "unsafe_url")
        self.assertFalse(result["body_acquired"])

    def test_public_address_is_pinned_while_tls_and_host_remain_original_hostname(self):
        import requests
        adapter = self.body.PinnedHTTPAdapter("publisher.example", "93.184.216.34")
        prepared = requests.Request("GET", self.item["url"]).prepare()
        pool = adapter.get_connection_with_tls_context(prepared, verify=True)
        self.assertEqual(pool.host, "93.184.216.34")
        self.assertEqual(pool.assert_hostname, "publisher.example")
        self.assertEqual(pool.conn_kw["server_hostname"], "publisher.example")
        adapter.close()

    def test_hidden_full_text_is_not_used_to_expand_visible_teaser(self):
        for hidden in ('hidden', 'aria-hidden="true"', 'style="display: none"', 'style="visibility:hidden"'):
            html = HTML.split(b"<article>")[0] + (
                '<article><h1>Publisher headline</h1><p>A brief teaser.</p>'
                '<div ' + hidden + '><p>' + PROSE + '</p></div></article></body></html>').encode()
            with self.subTest(hidden=hidden):
                result, _, _ = self.fetch(FakeResponse(html))
                self.assertFalse(result["body_acquired"])
                self.assertFalse(result["eligible"])
                self.assertEqual(result["paragraphs"], [])

    def test_table_merged_cell_structure_survives_text_extraction(self):
        html = HTML.replace(b"<th>Segment</th>", b'<th rowspan="2">Segment</th>')
        html = html.replace(b"<th>Revenue (USD millions)</th>", b'<th colspan="2">Revenue (USD millions)</th>')
        result, _, _ = self.fetch(FakeResponse(html))
        table = next(p for p in result["paragraphs"] if p["kind"] == "table")
        self.assertEqual(table["table"]["rows"][0][0]["rowspan"], 2)
        self.assertEqual(table["table"]["rows"][0][1]["colspan"], 2)
        self.assertTrue(table["table"]["rows"][0][1]["header"])
        self.assertEqual(table["table"]["caption"], "Q3 2026 segment revenue; unaudited")

    def test_archive_failure_returns_ineligible_candidate_instead_of_throwing(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("private filesystem detail")):
            result, _, _ = self.fetch()
        self.assertEqual(result["status"], "archive_unavailable")
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["raw_path"])
        self.assertNotIn("private filesystem detail", json.dumps(result))

    def test_raw_archive_tampering_is_not_overwritten_or_accepted(self):
        first, _, _ = self.fetch()
        raw = Path(first["raw_path"])
        raw.write_bytes(b"tampered archive")
        second, _, _ = self.fetch()
        self.assertEqual(second["status"], "archive_conflict")
        self.assertFalse(second["eligible"])
        self.assertEqual(raw.read_bytes(), b"tampered archive")


if __name__ == "__main__":
    unittest.main()
