"""Offline publisher markup repair: no model, embedding, DB or network calls."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
TECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TECH))


class PublisherScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("integration.scope_context"):
            from integration import scope_context
            cls.module = scope_context
        else:
            cls.module = None

    def setUp(self):
        self.assertIsNotNone(self.module, "publisher scope qualifier is not implemented")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for part in ("raw", "text", "output"):
            (self.root / part).mkdir()
        self.html = (b"<html><main><h2>Quarterly Results</h2>"
                     b"<p>Company revenue was $96.2 billion.</p>"
                     b"<p><strong>Data Center</strong></p>"
                     b"<p>Second-quarter revenue was $89.0 billion.</p>"
                     b"<p><strong>Not a heading.</strong></p>"
                     b"<p><strong>Misleading</strong> additional prose.</p>"
                     b"<p>Follow-up.</p><h2>Next</h2><p>New follow-up.</p>"
                     b"</main></html>")
        self.texts = ["Quarterly Results", "Company revenue was $96.2 billion.",
                      "Data Center", "Second-quarter revenue was $89.0 billion.",
                      "Not a heading.", "Misleading additional prose.", "Follow-up.",
                      "Next", "New follow-up."]
        self.source = dict(source_id="S", local_path="raw/S.html", text_path="text/S.jsonl",
                           sha256=hashlib.sha256(self.html).hexdigest(),
                           available_at="2026-08-27T12:00:00+00:00",
                           retrieved_at="2026-10-17T12:00:00+00:00",
                           historical_version_verified=False, historical_eligible=False,
                           version_group="S")
        (self.root / "raw/S.html").write_bytes(self.html)
        self.blocks = [dict(source_id="S", pdf_page=None, paragraph=n, text=text)
                       for n, text in enumerate(self.texts, 1)]
        (self.root / "text/S.jsonl").write_text(
            "\n".join(json.dumps(b) for b in self.blocks), encoding="utf-8")
        (self.root / "output/source_catalog.json").write_text(
            json.dumps([self.source]), encoding="utf-8")
        self.root_patch = patch.object(self.module, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.at = "2026-10-18T15:38:11+00:00"

    def record(self, paragraph):
        text = self.texts[paragraph - 1]
        return dict(source_id="S", source_sha256=self.source["sha256"],
                    pdf_page=None, paragraph=paragraph, char_start=0,
                    char_end=len(text), text=text, available_at=self.source["available_at"])

    def qualify(self, paragraph, **kwargs):
        return self.module.qualify_record(self.record(paragraph),
                                          kwargs.get("at", self.at), kwargs.get("historical", False))

    def test_total_company_96_2_and_datacenter_89_have_different_publisher_context(self):
        # Break caught: later Data Center markup incorrectly scopes company revenue.
        whole = self.qualify(2)
        segment = self.qualify(4)
        self.assertEqual(whole["status"], "verified")
        self.assertNotIn("Data Center", [x["text"] for x in whole["labels"]])
        dc = next(x for x in segment["labels"] if x["text"] == "Data Center")
        self.assertEqual((dc["paragraph"], dc["char_start"], dc["char_end"]), (3, 0, 11))
        self.assertEqual(dc["source_sha256"], self.source["sha256"])
        self.assertEqual(dc["publisher_markup"], "p/strong")
        self.assertEqual(segment["source_record"]["paragraph"], 4)

    def test_untrusted_scope_hints_do_not_map_or_repair_wrong_coordinates(self):
        # Break caught: semantic metadata is substituted for actual publisher markup.
        e = dict(self.record(2), business_scope="Data Center", section_hint="Data Center")
        untouched = deepcopy(e)
        got = self.module.qualify_record(e, self.at, False)
        self.assertNotIn("Data Center", [x["text"] for x in got["labels"]])
        self.assertEqual(e, untouched)
        e["paragraph"] = 4
        got = self.module.qualify_record(e, self.at, False)
        self.assertEqual(got["labels"], [])
        self.assertEqual(got["reason"], "record_quote_coordinate_mismatch")

    def test_history_and_future_are_filtered_before_adding_labels(self):
        # Break caught: current mutable HTML leaks into historical/future-ineligible context.
        self.assertEqual(self.qualify(4, historical=True)["reason"], "historical_version_not_verified")
        self.assertEqual(self.qualify(4, at="2026-08-01T00:00:00+00:00")["labels"], [])
        self.assertEqual(self.qualify(4, at="2026-10-01T00:00:00+00:00")["reason"], "snapshot_not_collected_at_analysis_time")

    def test_raw_html_hash_change_fails_closed_even_when_text_would_stay_same(self):
        # Break caught: a cached parsed DOM bypasses immutable raw version verification.
        self.assertEqual(self.qualify(4)["status"], "verified")
        (self.root / "raw/S.html").write_bytes(self.html + b" ")
        got = self.qualify(4)
        self.assertEqual(got["labels"], [])
        self.assertEqual(got["reason"], "raw_sha256_mismatch")

    def test_partial_bold_and_full_sentences_are_not_labels_and_heading_resets_scope(self):
        # Break caught: inline bold prose or obsolete sibling scope becomes a label.
        self.assertEqual([x["text"] for x in self.qualify(7)["labels"]],
                         ["Quarterly Results", "Data Center"])
        self.assertEqual([x["text"] for x in self.qualify(9)["labels"]], ["Next"])

    def test_any_dom_archive_mismatch_and_pdf_have_no_verified_html_labels(self):
        # Break caught: labels are attached from a DOM with a different paragraph mapping.
        path = self.root / "text/S.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace("Follow-up.", "Other."), encoding="utf-8")
        self.assertEqual(self.qualify(4)["reason"], "html_text_archive_mismatch")
        e = dict(self.record(4), pdf_page=1)
        self.assertEqual(self.module.qualify_record(e, self.at, False)["labels"], [])


class RealArchiveScopeTests(unittest.TestCase):
    def test_original_nvda_label_is_present_in_actual_model_messages_with_budget_counted(self):
        # Break caught: the qualifier exists but its label never reaches actual messages.
        self.assertIsNotNone(importlib.util.find_spec("integration.scope_context"),
                             "publisher scope qualifier is not implemented")
        from integration.scope_context import qualify_record
        from integration.effect_context import build_model_context, ContextBudget
        from integration.context_codec import unpack_context
        source = next(s for s in json.loads((TECH / "output/source_catalog.json").read_text(encoding="utf-8"))
                      if s["source_id"] == "NVDA_2027_Q2_RELEASE")
        blocks = [json.loads(x) for x in (TECH / source["text_path"]).read_text(encoding="utf-8").splitlines()]
        def record(n):
            raw = next(b["text"] for b in blocks if b.get("paragraph") == n)
            text = " ".join(raw.split())
            return dict(source_id=source["source_id"], source_sha256=source["sha256"],
                        pdf_page=None, paragraph=n, char_start=0, char_end=len(text),
                        text=text, available_at=source["available_at"], registry_kind="exact_original_quote")
        at = "2026-10-18T15:38:11+00:00"
        dc = qualify_record(record(20), at, False)
        self.assertIn("Data Center", [x["text"] for x in dc["labels"]])
        self.assertNotIn("Data Center", [x["text"] for x in qualify_record(record(5), at, False)["labels"]])
        state = dict(evidence_registry={"C_scope": record(20)}, claim_ledger={},
                     received_event={"citation_ids": ["C_scope"]}, unresolved_gaps=[], review_issues=[])
        before = deepcopy(state)
        built = build_model_context(state, "news", "NVDA", "quarterly revenue", at, False, [])
        sent = json.loads(built["messages"][-1]["content"].split("TIME_FROZEN_RESEARCH_DATA\n", 1)[1])
        sent = unpack_context(sent)
        self.assertEqual(sent["evidence"]["C_scope"]["publisher_scope"], dc)
        self.assertEqual(built["input_chars"], sum(len(m["content"]) for m in built["messages"]))
        self.assertEqual(state, before)
        with self.assertRaises(ContextBudget):
            build_model_context(state, "news", "NVDA", "quarterly revenue", at, False, [], max_chars=100)


if __name__ == "__main__":
    unittest.main()
