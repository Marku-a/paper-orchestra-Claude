# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from utils import citation_guard as cg

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">%s</feed>"""
ATOM_ENTRY = """<entry><id>http://arxiv.org/abs/1706.03762v7</id>
<published>2017-06-12T17:57:34Z</published><title>%s</title></entry>"""


def _resp(status=200, json_data=None, content=b""):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = json_data or {}
    r.content = content
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status))
    return r


def _s2(*titles):
    return _resp(
        json_data={
            "data": [{"title": t, "year": 2017, "url": "http://s2/x"} for t in titles]
        }
    )


BIB = """@article{vaswani2017,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and {Google Brain}},
  year = {2017}
}

@inproceedings{fake2020,
  title = "A {Totally} Made Up Paper About Nothing",
  year = {2020}
}
"""


class _Base(unittest.TestCase):
    def setUp(self):
        for name in ("S2_SLEEP_SEC",):
            p = mock.patch.object(cg, name, 0)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(cg, "BACKOFF_SEC", (0, 0, 0))
        p.start()
        self.addCleanup(p.stop)


class ParseTest(unittest.TestCase):
    def test_nested_braces_and_quotes(self):
        entries = cg.parse_bibtex(BIB)
        self.assertEqual([e["key"] for e in entries], ["vaswani2017", "fake2020"])
        self.assertEqual(entries[0]["type"], "article")
        self.assertEqual(entries[0]["fields"]["author"], "Vaswani, Ashish and {Google Brain}")
        self.assertEqual(entries[0]["fields"]["year"], "2017")
        self.assertEqual(entries[1]["fields"]["title"], "A {Totally} Made Up Paper About Nothing")
        self.assertTrue(entries[1]["raw"].startswith("@inproceedings{fake2020"))


class NitsTest(_Base):
    def test_clean_query_title(self):
        self.assertEqual(
            cg._clean_query_title('\\emph{X} and {BERT}: "Pre" \\& more'), "X and BERT: Pre & more"
        )

    def test_add_note_merges_existing(self):
        raw = "@misc{k,\n  title = {T},\n  note = foo\n}"
        out = cg._add_note(raw, "not_found")
        self.assertEqual(out.count("note"), 1)
        self.assertIn("note = {CITATION-GUARD: UNVERIFIED (not_found); } # foo", out)
        out2 = cg._add_note("@misc{k,\n note = {abc}\n}", "not_found")
        self.assertIn("note = {CITATION-GUARD: UNVERIFIED (not_found); abc}", out2)

    def test_unparsed_reported(self):
        d = tempfile.mkdtemp()
        bib = os.path.join(d, "r.bib")
        with open(bib, "w") as f:
            f.write("@comment{x}\n@article(old, title = {Attention Is All You Need})\n" + BIB)
        with mock.patch.object(cg.requests, "get", return_value=_s2("Attention Is All You Need")):
            summary = cg.guard_bib_file(bib)
        self.assertEqual(summary["unparsed"], 1)
        self.assertEqual(summary["total"], 2)
        self.assertIn("@article(old", open(os.path.join(d, "citation_report.md")).read())


class VerifyTest(_Base):
    ENTRY = {"fields": {"title": "Attention Is All You Need", "year": "2017"}}

    def test_s2_hit(self):
        with mock.patch.object(cg.requests, "get", return_value=_s2("Attention is all you need")):
            r = cg.verify_entry(self.ENTRY)
        self.assertEqual((r["status"], r["source"]), ("verified", "semantic_scholar"))

    def test_s2_miss_arxiv_hit(self):
        xml = (ATOM % (ATOM_ENTRY % "Attention Is All\n  You Need")).encode()
        get = mock.Mock(side_effect=[_s2("Something else entirely"), _resp(content=xml)])
        with mock.patch.object(cg.requests, "get", get):
            r = cg.verify_entry(self.ENTRY)
        self.assertEqual((r["status"], r["source"]), ("verified", "arxiv"))
        self.assertIn("arxiv.org", r["url"])

    def test_year_mismatch_rejected(self):
        get = mock.Mock(side_effect=[_s2("Attention Is All You Need"), _resp(content=(ATOM % "").encode())])
        entry = {"fields": {"title": "Attention Is All You Need", "year": "2010"}}
        with mock.patch.object(cg.requests, "get", get):
            self.assertEqual(cg.verify_entry(entry)["status"], "not_found")

    def test_retry_on_429(self):
        get = mock.Mock(side_effect=[_resp(429), _s2("Attention Is All You Need")])
        with mock.patch.object(cg.requests, "get", get):
            r = cg.verify_entry(self.ENTRY)
        self.assertEqual(r["status"], "verified")
        self.assertEqual(get.call_count, 2)

    def test_api_key_header(self):
        get = mock.Mock(return_value=_s2("Attention Is All You Need"))
        with mock.patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "k"}), mock.patch.object(
            cg.requests, "get", get
        ):
            cg.verify_entry(self.ENTRY)
        self.assertEqual(get.call_args.kwargs["headers"], {"x-api-key": "k"})

    def test_network_error(self):
        with mock.patch.object(cg.requests, "get", side_effect=requests.ConnectionError("down")):
            r = cg.verify_entry(self.ENTRY)
        self.assertEqual(r["status"], "lookup_failed")


class GuardFileTest(_Base):
    def _run(self, strict=False):
        d = tempfile.mkdtemp()
        bib = os.path.join(d, "references.bib")
        with open(bib, "w") as f:
            f.write(BIB)

        def get(url, **kw):
            if "semanticscholar" in url:
                q = kw["params"]["query"]
                return _s2(q) if "Attention" in q else _s2()
            return _resp(content=(ATOM % "").encode())

        with mock.patch.object(cg.requests, "get", side_effect=get):
            if strict:
                with self.assertRaisesRegex(ValueError, "fake2020"):
                    cg.guard_bib_file(bib, strict=True)
                summary = None
            else:
                summary = cg.guard_bib_file(bib)
        return d, bib, summary

    def test_not_found_flagged_and_reported(self):
        d, bib, summary = self._run()
        self.assertEqual(summary["verified"], 1)
        self.assertEqual(summary["not_found"], 1)
        text = open(bib).read()
        self.assertIn("note = {CITATION-GUARD: UNVERIFIED (not_found)}", text)
        self.assertEqual(text.count("CITATION-GUARD"), 1)
        self.assertIn("fake2020", text)
        report = open(os.path.join(d, "citation_report.md")).read()
        self.assertIn("| fake2020 |", report)
        self.assertIn("not_found", report)
        self.assertEqual(len(cg.parse_bibtex(text)), 2)

    def test_strict_raises_after_report(self):
        d, _, _ = self._run(strict=True)
        self.assertTrue(os.path.exists(os.path.join(d, "citation_report.md")))


if __name__ == "__main__":
    unittest.main()


class AddNoteTests(unittest.TestCase):
    def test_existing_note_is_prepended_not_duplicated(self):
        from utils.citation_guard import _add_note

        out = _add_note('@article{a,\n  title={X},\n  note = {old}\n}', "not_found")
        self.assertEqual(out.lower().count("note ="), 1)
        self.assertIn("UNVERIFIED (not_found); old", out)

    def test_note_added_when_missing(self):
        from utils.citation_guard import _add_note

        out = _add_note("@article{b,\n  title={Y}\n}", "lookup_failed")
        self.assertIn("note = {CITATION-GUARD: UNVERIFIED (lookup_failed)}", out)
