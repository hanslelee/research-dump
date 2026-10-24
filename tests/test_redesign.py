"""Tests for marks/migration, bundle, dedupe, compare, topic recommendation and requests."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import addpaper  # noqa: E402
import bundle  # noqa: E402
import claimrel as claimrel_mod  # noqa: E402
import collect  # noqa: E402
import compare  # noqa: E402
import dedupe  # noqa: E402
import migrate  # noqa: E402
import notify  # noqa: E402
import requests_run  # noqa: E402
import screen  # noqa: E402
import topics  # noqa: E402
from common import matched_keywords  # noqa: E402

AGENT = {"base_url": "https://x.test/v1", "key_name": "K", "model": "m"}
CONFIG = {"scope": "LLM safety", "topics": ["sycophancy", "steering", "calibration"],
          "llm": {"screening": AGENT, "summary": AGENT, "compare": {**AGENT, "model": "SET-ME"}},
          "collect": {"arxiv_keywords": ["sycophancy", "activation steering"]}}


def paper(pid, title, topic=(), liked=False, entered="2026-10-01", **extra):
    return {"id": pid, "title": title, "authors": [], "year": 2026, "abstract": f"abstract of {title}",
            "tags": {"method": ["analysis"], "main_method": "analysis", "goal": [], "modal": "text", "setting": "standalone",
                     "topic": list(topic)},
            "summary": {"claim": f"claim of {title}", "condition": "c", "gap": "g"},
            "marks": {"liked": liked, "bookmarked": False, "hidden": False}, "memo": "", "entered_at": entered,
            "source": "collect", "provenance": {}, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "config.json").write_text(json.dumps(CONFIG))
        for sub in ("papers", "relations", "claims", "authors"):
            (self.d / sub).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, sub, obj):
        (self.d / sub / (obj["id"].replace(":", "_").replace("/", "_") + ".json")).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

    def load(self, sub):
        return {json.loads(f.read_text())["id"]: json.loads(f.read_text()) for f in (self.d / sub).glob("*.json")}


class MigrateTest(Base):
    def test_status_becomes_marks(self):
        for pid, status in (("a:1", "confirmed"), ("a:2", "hold"), ("a:3", "new")):
            self.put("papers", {"id": pid, "title": pid, "status": status, "provenance": {"created_at": "2026-10-10"}})
        self.put("claims", {"id": "claim:1", "text": "t", "status": "active", "version": 1})
        self.assertEqual(migrate.run(self.d, "2026-10-24", log=lambda *_: None), 4)
        p = self.load("papers")
        self.assertEqual(p["a:1"]["marks"], {"liked": True, "bookmarked": False, "hidden": False, "liked_at": "2026-10-10"})
        self.assertTrue(p["a:2"]["marks"]["bookmarked"])
        self.assertEqual(p["a:3"]["marks"], {"liked": False, "bookmarked": False, "hidden": False})
        self.assertTrue(all("status" not in x and x["entered_at"] == "2026-10-10" and x["memo"] == "" for x in p.values()))
        self.assertEqual(self.load("claims")["claim:1"]["memo"], "")
        self.assertEqual(migrate.run(self.d, "2026-10-24", log=lambda *_: None), 0)  # idempotent


class BundleTest(Base):
    def test_sha_matches_git_and_excluded_listed(self):
        self.put("papers", paper("arxiv:1", "Kept"))
        (self.d / "candidates").mkdir()
        (self.d / "candidates" / "2026-10-20.json").write_text(json.dumps({"items": [
            {"id": "arxiv:2", "title": "Dropped", "abstract": "a", "authors": ["X"], "url": "u"},
            {"id": "arxiv:1", "title": "Kept", "abstract": "a"}]}))
        (self.d / "logs" / "screening").mkdir(parents=True)
        (self.d / "logs" / "screening" / "2026-10-20.json").write_text(json.dumps([
            {"id": "arxiv:2", "title": "Dropped", "in_scope": False, "reason": "off"},
            {"id": "arxiv:1", "title": "Kept", "in_scope": True, "reason": "ok"}]))
        out = bundle.run(self.d, "2026-10-24", log=lambda *_: None)
        b = json.loads(out.read_text())
        f = self.d / "papers" / "arxiv_1.json"
        git_sha = subprocess.run(["git", "hash-object", str(f)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(b["entries"]["papers/arxiv_1.json"]["sha"], git_sha)
        self.assertEqual([x["id"] for x in b["excluded"]], ["arxiv:2"])
        self.assertEqual(b["excluded"][0]["authors"], ["X"])
        self.assertEqual(b["excluded"][0]["screen_reason"], "off")


class DedupeTest(Base):
    def test_merges_versions_and_moves_relations(self):
        self.put("papers", paper("arxiv:1", "Same Title!", ["steering"], entered="2026-10-01"))
        dup = paper("doi:10.1/x", "Same title", ["calibration"], liked=True, entered="2026-10-05", venue="ICLR",
                    ids={"arxiv": "1v2", "doi": "10.1/x"})
        dup["memo"] = "my note"
        self.put("papers", dup)
        self.put("papers", paper("arxiv:9", "Other"))
        self.put("relations", {"id": "rel:a", "from": "arxiv:9", "to": "arxiv:1", "layer": "0", "type": "extends", "reason": "r", "status": "approved"})
        self.put("relations", {"id": "rel:b", "from": "arxiv:9", "to": "doi:10.1/x", "layer": "0", "type": "extends", "reason": "r", "status": "new"})
        self.assertEqual(dedupe.run(self.d, "2026-10-24", log=lambda *_: None), 1)
        p = self.load("papers")
        self.assertEqual(sorted(p), ["arxiv:9", "doi:10.1/x"])  # the one you liked and wrote a memo on is kept
        kept = p["doi:10.1/x"]
        self.assertTrue(kept["marks"]["liked"])
        self.assertEqual(kept["tags"]["topic"], ["calibration", "steering"])
        self.assertEqual(kept["entered_at"], "2026-10-01")
        rels = self.load("relations")
        self.assertEqual(list(rels), ["rel:a"])  # the duplicate relation is dropped
        self.assertEqual(rels["rel:a"]["to"], "doi:10.1/x")


def fake_compare(agent, system, user, **kw):
    data = json.loads(user)
    rows = []
    for c in data["candidates"]:
        if "Builds" in c["title"]:
            rows.append({"id": c["id"], "type": "extends", "direction": "candidate_extends", "evidence": "B fixes A's gap"})
        elif "Contra" in c["title"]:
            rows.append({"id": c["id"], "type": "conflicts", "evidence": "opposite results"})
        elif "NoEvidence" in c["title"]:
            rows.append({"id": c["id"], "type": "extends"})
        else:
            rows.append({"id": c["id"], "type": "none"})
    return {"text": json.dumps({"relations": rows}), "model": "x.test/m"}


class CompareTest(Base):
    def test_weekly_new_vs_liked_sharing_topic(self):
        self.put("papers", paper("arxiv:new", "Builds on it", ["sycophancy"], entered="2026-10-22"))
        self.put("papers", paper("arxiv:old", "Liked base", ["sycophancy"], liked=True))
        self.put("papers", paper("arxiv:other", "Liked other topic", ["steering"], liked=True))
        calls = []
        chat = lambda a, s, u, **kw: (calls.append(json.loads(u)), fake_compare(a, s, u))[1]
        rels = compare.run(self.d, "2026-10-24", log=lambda *_: None, chat=chat)
        # new vs liked (same topic only) + each liked paper vs all papers sharing a topic
        pairs = {(c["target"]["id"], x["id"]) for c in calls for x in c["candidates"]}
        self.assertIn(("arxiv:new", "arxiv:old"), pairs)
        self.assertNotIn(("arxiv:new", "arxiv:other"), pairs)
        self.assertTrue(all(r["status"] == "new" and r["reason"] for r in rels))
        self.assertEqual(len(self.load("relations")), len({r["id"] for r in rels}))
        # second run asks nothing again
        calls.clear()
        compare.run(self.d, "2026-10-24", log=lambda *_: None, chat=chat)
        self.assertEqual(calls, [])

    def test_find_for_one_paper_no_topic_filter_and_needs_evidence(self):
        self.put("papers", paper("arxiv:t", "Target", ["steering"]))
        self.put("papers", paper("arxiv:c", "Contra study", ["calibration"]))
        self.put("papers", paper("arxiv:b", "Builds further", []))
        self.put("papers", paper("arxiv:n", "NoEvidence claim", []))
        rels = compare.find_for(self.d, "arxiv:t", "2026-10-24", log=lambda *_: None, chat=fake_compare)
        got = {(r["from"], r["to"], r["type"]) for r in rels}
        self.assertEqual(got, {("arxiv:t", "arxiv:c", "conflicts"), ("arxiv:b", "arxiv:t", "extends")})


def fake_topics(agent, system, user, **kw):
    assert "FOCUS PAPER arxiv:t" in user
    return {"text": json.dumps({"topics": [
        {"text": "Gate steering by probe confidence", "basis": "A meets B", "first_experiment": "7B, TriviaQA",
         "papers": ["arxiv:t", "arxiv:l", "unknown"]},
        {"text": "Second", "papers": []}]}), "model": "x.test/m"}


class TopicsTest(Base):
    def test_recommend_uses_liked_same_topic_when_no_links(self):
        self.put("papers", paper("arxiv:t", "Target", ["sycophancy"]))
        self.put("papers", paper("arxiv:l", "Liked", ["sycophancy"], liked=True))
        self.put("papers", paper("arxiv:x", "Liked elsewhere", ["steering"], liked=True))
        fetched = []
        claims = topics.recommend(self.d, "arxiv:t", "2026-10-24", log=lambda *_: None, chat=fake_topics,
                                  fetch=lambda aid, log: (fetched.append(aid), "FULL TEXT")[1])
        self.assertEqual(sorted(fetched), ["l", "t"])
        self.assertEqual(len(claims), 2)
        c = claims[0]
        self.assertEqual((c["status"], c["origin"], c["papers"]), ("candidate", "ai", ["arxiv:t", "arxiv:l"]))
        self.assertEqual(c["experiments"], [{"title": "첫 실험", "status": "plan", "setup": "7B, TriviaQA", "result": ""}])
        self.assertEqual(claims[1]["experiments"], [])
        rels = [r for r in self.load("relations").values() if r["to"] == c["id"]]
        self.assertEqual({r["from"] for r in rels}, {"arxiv:t", "arxiv:l"})
        self.assertTrue(all(r["layer"] == "0-1" and r["type"] == "motivates" and r["status"] == "new" for r in rels))

    def test_pick_prefers_approved_links(self):
        ps = {p["id"]: p for p in (paper("arxiv:t", "T", ["sycophancy"]), paper("arxiv:a", "A"), paper("arxiv:l", "L", ["sycophancy"], liked=True))}
        rels = {"r": {"id": "r", "from": "arxiv:a", "to": "arxiv:t", "layer": "0", "type": "extends", "status": "approved"},
                "r2": {"id": "r2", "from": "arxiv:l", "to": "arxiv:t", "layer": "0", "type": "conflicts", "status": "new"}}
        chosen, how = topics.pick_papers(ps, rels, "arxiv:t")
        self.assertEqual(([p["id"] for p in chosen], how), (["arxiv:t", "arxiv:a"], "linked"))


class RequestsTest(Base):
    def test_done_requests_removed_failed_kept(self):
        self.put("papers", paper("arxiv:t", "Target", ["steering"]))
        self.put("papers", paper("arxiv:c", "Contra study", ["steering"]))
        (self.d / "requests").mkdir()
        self.put("requests", {"id": "req:1", "kind": "relations", "paper": "arxiv:t", "status": "pending"})
        self.put("requests", {"id": "req:2", "kind": "topics", "paper": "arxiv:missing", "status": "pending"})
        n = requests_run.run(self.d, "2026-10-24", log=lambda *_: None, chat=fake_compare)
        self.assertEqual(n, 1)
        left = self.load("requests")
        self.assertEqual(list(left), ["req:2"])
        self.assertEqual(left["req:2"]["status"], "failed")
        self.assertIn("not found", left["req:2"]["error"])
        self.assertTrue((self.d / "bundle.json").exists())


class KeywordTest(Base):
    def test_matched_keywords(self):
        self.assertEqual(matched_keywords("We study Activation-Steering and sycophancy.", ["sycophancy", "activation steering", "jailbreak"]),
                         ["sycophancy", "activation steering"])

    def test_screening_counts_keywords(self):
        (self.d / "candidates").mkdir()
        (self.d / "candidates" / "2026-10-24.json").write_text(json.dumps({"items": [
            {"id": "arxiv:1", "title": "Sycophancy probe", "abstract": "sycophancy"},
            {"id": "arxiv:2", "title": "Sycophancy in cooking", "abstract": ""}]}))

        def chat(agent, system, user, **kw):
            rows = [{"id": c["id"], "in_scope": c["id"] == "arxiv:1", "reason": "r",
                     "tags": {"method": ["analysis"], "topic": ["sycophancy"]}} for c in json.loads(user)]
            return {"text": json.dumps({"results": rows}), "model": "m"}
        screen.run(self.d, "2026-10-24", log=lambda *_: None, chat=chat)
        kw = json.loads((self.d / "state" / "keywords.json").read_text())
        self.assertEqual(kw["last_run"]["sycophancy"], {"collected": 2, "included": 1})
        self.assertEqual(notify.keyword_line(kw["last_run"]), "sycophancy 1")

    def test_included_paper_gets_tags(self):
        p = paper("arxiv:5", "Included by me")
        p["provenance"] = {"needs_tags": True}
        self.put("papers", p)
        seen = []

        def chat(agent, system, user, **kw):
            seen.append(system)
            return {"text": json.dumps({"results": [{"id": "arxiv:5", "in_scope": True, "reason": "r",
                                                    "tags": {"method": ["eval"], "topic": ["calibration"]}}]}), "model": "m"}
        screen.run(self.d, "2026-10-24", log=lambda *_: None, chat=chat)
        got = self.load("papers")["arxiv:5"]
        self.assertEqual(got["tags"]["method"], ["eval"])
        self.assertNotIn("needs_tags", got["provenance"])
        self.assertIn("already chose to keep", seen[0])


class SeedTest(unittest.TestCase):
    def test_citation_seeds_are_liked_or_starred(self):
        ps = {"a": paper("arxiv:a", "A", liked=True), "b": paper("arxiv:b", "B", meta={"popularity": "★"}),
              "c": paper("arxiv:c", "C"), "d": {"id": "arxiv:d", "title": "D", "status": "confirmed"}}
        asked = []
        fake = lambda url, headers=None: (asked.append(url.split("/paper/")[1].split("/")[0]), {"data": []})[1]
        with mock.patch.object(collect, "get_json", fake), mock.patch.object(collect.time, "sleep", lambda *_: None), \
                mock.patch.dict(os.environ, {"S2_API_KEY": "k"}):
            collect.collect_s2({**collect.DEFAULT_COLLECT, "s2_citations_of": "confirmed"}, ps, "2026-10-01", lambda *_: None)
        self.assertEqual(sorted(asked), ["arXiv:a", "arXiv:b", "arXiv:d"])


if __name__ == "__main__":
    unittest.main()


def fake_claimrel(agent, system, user, **kw):
    data = json.loads(user)
    if "research_topic" not in data:  # paper vs paper (관계 찾기)
        return fake_compare(agent, system, user, **kw)
    rows = []
    for p in data["papers"]:
        if "CAA" in p["title"]:
            rows.append({"id": p["id"], "type": "motivates", "evidence": "CAA gives the sycophancy vector"})
        elif "Already" in p["title"]:
            rows.append({"id": p["id"], "type": "duplicate", "evidence": "same idea"})
        elif "Vague" in p["title"]:
            rows.append({"id": p["id"], "type": "support"})  # no evidence -> dropped
        else:
            rows.append({"id": p["id"], "type": "none"})
    return {"text": json.dumps({"relations": rows}), "model": "x.test/m"}


def fake_all(agent, system, user, **kw):
    if "screen new research papers" in system:
        rows = [{"id": c["id"], "in_scope": True, "reason": "r", "tags": {"method": ["inference"], "topic": ["steering"]}} for c in json.loads(user)]
        return {"text": json.dumps({"results": rows}), "model": "m"}
    if "summarize one research paper" in system:
        return {"text": '{"claim": "주장", "condition": "조건", "gap": "빈 곳"}', "model": "m"}
    return fake_claimrel(agent, system, user, **kw)


CAA = {"id": "arxiv:2312.06681", "title": "Steering Llama 2 via CAA", "abstract": "We steer.", "authors": ["N. Rimsky"],
       "year": 2023, "venue": "arXiv", "url": "https://arxiv.org/abs/2312.06681", "published": "2023-12-09",
       "ids": {"arxiv": "2312.06681"}, "sources": ["manual"]}


class ClaimTest(Base):
    def setUp(self):
        super().setUp()
        self.put("claims", {"id": "claim:g", "text": "Confidence-gated steering", "status": "candidate", "version": 1, "basis": "b"})

    def test_claim_papers_strict_with_evidence(self):
        self.put("papers", paper("arxiv:1", "CAA vectors"))
        self.put("papers", paper("arxiv:2", "Already gated"))
        self.put("papers", paper("arxiv:3", "Vague one"))
        self.put("papers", paper("arxiv:4", "Cooking"))
        rels = claimrel_mod.link(self.d, "claim:g", "2026-10-24", log=lambda *_: None, chat=fake_claimrel)
        self.assertEqual({(r["from"], r["type"]) for r in rels}, {("arxiv:1", "motivates"), ("arxiv:2", "duplicate")})
        self.assertTrue(all(r["layer"] == "0-1" and r["to"] == "claim:g" and r["status"] == "new" for r in rels))
        # already linked papers are not asked again
        calls = []
        claimrel_mod.link(self.d, "claim:g", "2026-10-24", log=lambda *_: None,
                          chat=lambda a, s, u, **kw: (calls.append(json.loads(u)), fake_claimrel(a, s, u))[1])
        self.assertEqual(sorted(p["id"] for c in calls for p in c["papers"]), ["arxiv:3", "arxiv:4"])

    def test_add_paper_from_claim_screen(self):
        self.put("papers", paper("arxiv:5", "Contra study", ["steering"]))
        (self.d / "requests").mkdir()
        self.put("requests", {"id": "req:a", "kind": "add_paper", "query": "2312.06681", "claim": "claim:g", "status": "pending"})
        requests_run.run(self.d, "2026-10-24", log=lambda *_: None, chat=fake_all, find=lambda q, log: dict(CAA))
        p = self.load("papers")["arxiv:2312.06681"]
        self.assertEqual(p["summary"]["claim"], "주장")
        self.assertEqual(p["tags"]["topic"], ["steering"])
        self.assertNotIn("needs_tags", p["provenance"])
        rels = self.load("relations").values()
        self.assertIn(("arxiv:2312.06681", "claim:g", "motivates"), {(r["from"], r["to"], r["type"]) for r in rels})
        self.assertIn(("arxiv:2312.06681", "arxiv:5", "conflicts"), {(r["from"], r["to"], r["type"]) for r in rels})
        self.assertEqual(self.load("requests"), {})

    def test_add_paper_from_list_links_all_open_claims_only(self):
        self.put("claims", {"id": "claim:old", "text": "old", "status": "retired", "version": 1})
        self.put("papers", paper("arxiv:5", "Other", ["steering"]))
        asked = []

        def chat(a, s, u, **kw):
            if "research_topic" in u:
                asked.append(json.loads(u)["research_topic"]["topic"])
            if "TARGET" in s:
                raise AssertionError("no paper-vs-paper comparison from the list tab")
            return fake_all(a, s, u, **kw)
        (self.d / "requests").mkdir()
        self.put("requests", {"id": "req:b", "kind": "add_paper", "query": "Steering Llama 2 via CAA", "status": "pending"})
        requests_run.run(self.d, "2026-10-24", log=lambda *_: None, chat=chat, find=lambda q, log: dict(CAA))
        self.assertEqual(asked, ["Confidence-gated steering"])

    def test_existing_paper_not_duplicated(self):
        self.put("papers", paper("arxiv:2312.06681", "Steering Llama 2 via CAA"))
        pid, added = addpaper.add(self.d, "2312.06681", "2026-10-24", log=lambda *_: None, chat=fake_all, find=lambda q, log: dict(CAA))
        self.assertEqual((pid, added), ("arxiv:2312.06681", False))

    def test_arxiv_id_parsing(self):
        self.assertEqual(addpaper.arxiv_id_in("https://arxiv.org/abs/2312.06681v2"), "2312.06681")
        self.assertEqual(addpaper.arxiv_id_in("2312.06681"), "2312.06681")
        self.assertEqual(addpaper.arxiv_id_in("Steering Llama 2 via Contrastive Activation Addition"), "")

    def test_migrate_moves_gap_and_first_experiment(self):
        self.put("claims", {"id": "claim:m", "text": "t", "status": "candidate", "version": 1, "basis": "why",
                            "gap": "nobody did", "first_experiment": "7B test", "experiments": [{"title": "done", "result": "ok"}]})
        migrate.run(self.d, "2026-10-24", log=lambda *_: None)
        c = self.load("claims")["claim:m"]
        self.assertEqual(c["basis"], "why\n빈 곳: nobody did")
        self.assertNotIn("gap", c)
        self.assertEqual([(x["title"], x["status"]) for x in c["experiments"]], [("첫 실험", "plan"), ("done", "done")])

    def test_dropped_request_is_deleted(self):
        (self.d / "requests").mkdir()
        self.put("requests", {"id": "req:d", "kind": "relations", "paper": "x", "status": "dropped"})
        requests_run.run(self.d, "2026-10-24", log=lambda *_: None, chat=fake_all)
        self.assertEqual(self.load("requests"), {})
