"""Offline tests for enrich / screen / summarize / notify (LLM and HTTP are faked)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import collect  # noqa: E402
import enrich  # noqa: E402
import llm  # noqa: E402
import notify  # noqa: E402
import screen  # noqa: E402
import summarize  # noqa: E402

AGENT = {"base_url": "https://x.test/v1", "key_name": "K", "model": "m"}
CONFIG = {"scope": "LLM safety", "topics": ["jailbreak", "calibration"],
          "llm": {"screening": AGENT, "summary": AGENT}, "notify": {"viewer_url": "https://v.test/"}}


def fake_screen(agent, system, user):
    items = json.loads(user)
    res = []
    for it in items:
        if "safety" in it["title"].lower():
            res.append({"id": it["id"], "in_scope": True, "reason": "about safety",
                        "tags": {"method": ["inference", "bogus"], "goal": ["harmlessness"], "modal": "video",
                                 "setting": "rag", "topic": ["jailbreak", "made-up"]}})
        elif "skip" not in it["title"].lower():
            res.append({"id": it["id"], "in_scope": False, "reason": "off topic"})
    return {"text": "```json\n" + json.dumps({"results": res}) + "\n```", "model": "x.test/m"}


def fake_summary(agent, system, user):
    if "Partial" in user:
        return {"text": '{"claim": "c", "condition": "", "gap": "g"}', "model": "x.test/m"}
    return {"text": 'Sure! {"claim": "주장", "condition": "조건", "gap": "빈 곳"}', "model": "x.test/m"}


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "config.json").write_text(json.dumps(CONFIG))
        (self.d / "papers").mkdir()
        (self.d / "candidates").mkdir()
        mk = lambda i, t: {"id": i, "title": t, "abstract": "abs", "authors": ["A", "B"], "year": 2026, "url": "u", "sources": ["arxiv"]}
        (self.d / "candidates" / "2026-10-14.json").write_text(json.dumps({"items": [
            mk("arxiv:1", "A safety method"), mk("arxiv:2", "Cooking robots"), mk("arxiv:3", "Skip me please"),
        ]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_screen_adds_in_scope_and_logs_all(self):
        added = screen.run(self.d, "2026-10-15", log=lambda *_: None, chat=fake_screen)
        self.assertEqual([p["id"] for p in added], ["arxiv:1"])
        p = json.loads((self.d / "papers" / "arxiv_1.json").read_text())
        self.assertEqual(p["status"], "new")
        self.assertEqual(p["author_names"], ["A", "B"])
        self.assertEqual(p["tags"], {"method": ["inference"], "main_method": "inference", "goal": ["harmlessness"],
                                     "modal": "text", "setting": "rag", "topic": ["jailbreak"]})
        log = json.loads((self.d / "logs" / "screening" / "2026-10-15.json").read_text())
        self.assertEqual({d["id"]: d["in_scope"] for d in log}, {"arxiv:1": True, "arxiv:2": False})
        # arxiv:3 got no answer -> retried next run, the others are not
        calls = []
        screen.run(self.d, "2026-10-16", log=lambda *_: None, chat=lambda a, s, u: (calls.append(u), fake_screen(a, s, u))[1])
        self.assertEqual([i["id"] for i in json.loads(calls[0])], ["arxiv:3"])

    def test_summarize_writes_and_flags_incomplete(self):
        for pid, title, abstract in (("arxiv:9", "Full", "x"), ("arxiv:8", "Partial", "y"), ("acl:1", "No abstract", "")):
            (self.d / "papers" / f"{pid.replace(':', '_')}.json").write_text(json.dumps(
                {"id": pid, "title": title, "abstract": abstract, "status": "new", "summary": {"claim": ""}}))
        done = summarize.run(self.d, "2026-10-15", log=lambda *_: None, chat=fake_summary)
        self.assertEqual(sorted(p["id"] for p in done), ["arxiv:8", "arxiv:9"])
        full = json.loads((self.d / "papers" / "arxiv_9.json").read_text())
        self.assertEqual(full["summary"], {"claim": "주장", "condition": "조건", "gap": "빈 곳"})
        self.assertEqual(full["provenance"]["summary_check"], "ok")
        self.assertEqual(full["provenance"]["summary_from"], "abstract")
        part = json.loads((self.d / "papers" / "arxiv_8.json").read_text())
        self.assertEqual(part["provenance"]["summary_check"], "incomplete")
        # already summarized papers are not redone
        self.assertEqual(summarize.run(self.d, "2026-10-16", log=lambda *_: None, chat=fake_summary), [])

    def test_enrich_fills_arxiv_abstracts(self):
        (self.d / "papers" / "arxiv_2505.11049.json").write_text(json.dumps(
            {"id": "arxiv:2505.11049", "title": "G", "abstract": "", "status": "new"}))
        xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2505.11049v2</id>
        <published>2025-05-16T00:00:00Z</published><title>G</title><summary>The abstract.</summary>
        <author><name>Yue Liu</name></author></entry></feed>"""
        orig = collect.http_get
        collect.http_get = lambda url, headers=None, **k: xml
        enrich.time.sleep = lambda s: None
        os.environ.pop("S2_API_KEY", None)
        try:
            self.assertEqual(enrich.run(self.d, log=lambda *_: None), 1)
        finally:
            collect.http_get = orig
        p = json.loads((self.d / "papers" / "arxiv_2505.11049.json").read_text())
        self.assertEqual(p["abstract"], "The abstract.")
        self.assertEqual(p["author_names"], ["Yue Liu"])

    def test_parse_json_variants(self):
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json('Here you go: {"a": [1, 2]} hope it helps'), {"a": [1, 2]})
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json here")

    def test_unset_model_is_a_clear_error(self):
        os.environ["K"] = "k"
        with self.assertRaises(llm.LLMError) as e:
            llm.chat({**AGENT, "model": "SET-ME", "base_url": "http://127.0.0.1:9"}, "s", "u")
        self.assertIn("Set a model name", str(e.exception))

    def test_notify_messages(self):
        papers = [{"title": "T <1>", "url": "https://a", "summary": {"claim": "c & d"}, "provenance": {}}]
        msgs = notify.build_messages(papers, "https://v.test/")
        self.assertEqual(len(msgs), 1)
        self.assertIn("새 논문 1편", msgs[0])
        self.assertIn("T &lt;1&gt;", msgs[0])
        self.assertIn("c &amp; d", msgs[0])
        many = notify.build_messages(papers * 200, "")
        self.assertGreater(len(many), 1)
        self.assertTrue(all(len(m) <= 4096 for m in many))


if __name__ == "__main__":
    unittest.main()
