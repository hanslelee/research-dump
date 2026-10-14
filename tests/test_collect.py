"""Offline tests for pipeline/collect.py (all HTTP calls are faked).

    python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import collect  # noqa: E402

ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2610.11111v2</id>
    <published>2026-10-12T10:00:00Z</published>
    <title>A New   Jailbreak Defense
      for VLMs</title>
    <summary>We defend VLMs.</summary>
    <author><name>Alice</name></author><author><name>Bob</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2610.22222v1</id>
    <published>2026-10-11T10:00:00Z</published>
    <title>Shared Paper Found Twice</title>
    <summary>Also on S2.</summary>
    <author><name>Carol</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2505.11049v1</id>
    <published>2026-10-10T10:00:00Z</published>
    <title>Already In Papers</title>
    <summary>Known.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-01T10:00:00Z</published>
    <title>Too Old</title>
    <summary>Old.</summary>
  </entry>
</feed>"""

S2_JSON = {"data": [
    {"citingPaper": {"paperId": "abc", "title": "Shared paper found twice", "abstract": None, "year": 2026,
                     "venue": "", "publicationDate": "2026-10-11", "externalIds": {"ArXiv": "2610.22222"},
                     "authors": [{"name": "Carol"}]}},
    {"citingPaper": {"paperId": "def", "title": "Cites Confirmed Paper", "abstract": "x", "year": 2026,
                     "venue": "EMNLP", "publicationDate": "2026-10-09", "externalIds": {"DOI": "10.1/X"},
                     "authors": [{"name": "Dan"}]}},
]}

OPENALEX_JSON = {"results": [
    {"id": "https://openalex.org/W1", "title": "Lab Paper", "publication_year": 2026, "publication_date": "2026-10-08",
     "doi": "https://doi.org/10.2/Y", "abstract_inverted_index": {"safety": [1], "Model": [0]},
     "authorships": [{"author": {"display_name": "Eve"}}], "primary_location": {"source": {"display_name": "arXiv"}},
     "locations": [{"landing_page_url": "https://arxiv.org/abs/2610.33333"}]},
]}


def fake_http(calls):
    def http_get(url, headers=None, **kw):
        calls.append((url, headers or {}))
        host = urllib.parse.urlparse(url).hostname
        if host == "export.arxiv.org":
            return ARXIV_XML
        if host == "api.semanticscholar.org":
            assert headers.get("x-api-key") == "s2key"
            return json.dumps(S2_JSON).encode()
        if host == "api.openalex.org":
            assert "api_key=oakey" in url
            return json.dumps(OPENALEX_JSON).encode()
        raise AssertionError(url)
    return http_get


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "papers").mkdir()
        (self.d / "config.json").write_text(json.dumps({
            "scope": "x", "topics": [], "llm": {},
            "collect": {"arxiv_keywords": ["jailbreak", "over-refusal"], "openalex_institutions": ["I1"], "days": 8},
        }))
        for pid, status in (("arxiv:2505.11049", "confirmed"), ("arxiv:2504.02193", "new")):
            (self.d / "papers" / (pid.replace(":", "_") + ".json")).write_text(json.dumps(
                {"id": pid, "title": f"Paper {pid}", "status": status}))
        self.calls = []
        self.orig = collect.http_get
        collect.http_get = fake_http(self.calls)
        collect.time.sleep = lambda s: None
        os.environ["S2_API_KEY"] = "s2key"
        os.environ["OPENALEX_API_KEY"] = "oakey"

    def tearDown(self):
        collect.http_get = self.orig
        self.tmp.cleanup()

    def test_collect_merges_and_filters(self):
        items = collect.run(self.d, "2026-10-14", log=lambda *_: None)
        ids = {i["id"] for i in items}
        self.assertIn("arxiv:2610.11111", ids)            # version suffix stripped
        self.assertIn("arxiv:2610.22222", ids)            # found on arXiv and S2 -> one item
        self.assertIn("doi:10.1/x", ids)
        self.assertIn("arxiv:2610.33333", ids)            # OpenAlex arXiv link wins over DOI
        self.assertNotIn("arxiv:2505.11049", ids)         # already a paper
        self.assertEqual(len(items), 4)                   # too-old entry dropped
        shared = next(i for i in items if i["id"] == "arxiv:2610.22222")
        self.assertEqual(sorted(shared["sources"]), ["arxiv", "s2:cites:arxiv:2505.11049"])
        first = next(i for i in items if i["id"] == "arxiv:2610.11111")
        self.assertEqual(first["title"], "A New Jailbreak Defense for VLMs")
        self.assertEqual(first["authors"], ["Alice", "Bob"])
        lab = next(i for i in items if i["id"] == "arxiv:2610.33333")
        self.assertEqual(lab["abstract"], "Model safety")

        # only the confirmed paper is used as an S2 seed
        s2_urls = [u for u, _ in self.calls if "semanticscholar" in u]
        self.assertEqual(len(s2_urls), 1)
        self.assertIn("arXiv:2505.11049", s2_urls[0])

        out = json.loads((self.d / "candidates" / "2026-10-14.json").read_text())
        self.assertEqual(len(out["items"]), 4)
        self.assertEqual(out["since"], "2026-10-06")

    def test_second_run_skips_seen(self):
        collect.run(self.d, "2026-10-14", log=lambda *_: None)
        again = collect.run(self.d, "2026-10-15", log=lambda *_: None)
        self.assertEqual(again, [])
        state = json.loads((self.d / "state" / "collect.json").read_text())
        self.assertEqual(state["last_run"], "2026-10-15")

    def test_missing_keys_skip_sources(self):
        del os.environ["S2_API_KEY"]
        del os.environ["OPENALEX_API_KEY"]
        logs = []
        items = collect.run(self.d, "2026-10-14", log=logs.append)
        self.assertTrue(any("s2: skipped" in l for l in logs))
        self.assertTrue(any("openalex: skipped" in l for l in logs))
        self.assertEqual({i["sources"][0] for i in items}, {"arxiv"})

    def test_arxiv_query(self):
        q = collect.arxiv_query(["cs.CL"], ["jailbreak", "prompt injection"])
        self.assertEqual(q, '(cat:cs.CL) AND (abs:jailbreak OR abs:"prompt injection")')


if __name__ == "__main__":
    unittest.main()
