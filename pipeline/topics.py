"""연구 주제 추천: propose research topics from one paper and the papers linked to it.

    python pipeline/topics.py --data ../research-dump-data --paper <id>

Papers used (at most 5, the chosen paper first):
  1. papers linked to it by approved extends / conflicts relations
  2. if there are none: papers you liked that share a topic tag with it
  ranked 좋아요 -> ★ -> newest.
For arXiv papers the PDF is downloaded and its text (no figures) is sent to the model;
other papers use the abstract and summary. Your memos are not sent.

Each proposal (topic, basis, a planned first experiment) becomes a claim with status "candidate" and origin "ai" (연구 주제 탭, "AI 추천"),
linked to the papers it used by "motivates" relations with status "new".
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402
from common import clean_text, file_for, is_liked, is_starred, load_dir, topic_set, write_json  # noqa: E402

MAX_PAPERS = 5
MAX_CHARS = 60000  # per paper, about 15k tokens
N_TOPICS = 3

SYSTEM = """You help a researcher find new research topics.
You get up to five papers: the FOCUS paper and papers related to it. Read them and propose {n} research topics
that come from combining what these papers found and what they leave open.
Good topics are specific, testable with a small first experiment, and not already done by one of the papers.
Research scope of this researcher: {scope}

For each topic give:
- text: the research question or hypothesis in one sentence
- basis: which finding of which paper meets which finding of another to raise this question (name the papers),
  and what none of these papers has done yet
- first_experiment: the smallest experiment that would test it (model size, data, comparison, metric)
- papers: ids of the papers the topic uses
Write in {language}. Plain text, no markdown.
Reply with JSON only: {{"topics": [{{"text": "...", "basis": "...", "first_experiment": "...", "papers": ["..."]}}]}}"""


def arxiv_id(p):
    if p["id"].startswith("arxiv:"):
        return p["id"][6:]
    return (p.get("ids") or {}).get("arxiv") or ""


def pdf_text(aid, log=print):
    """Download an arXiv PDF and return its text, or "" when that fails."""
    try:
        from pypdf import PdfReader  # installed by the workflow
    except ImportError:
        log("topics: pypdf not installed, using abstracts")
        return ""
    try:
        base = re.sub(r"v\d+$", "", aid)
        req = urllib.request.Request(f"https://arxiv.org/pdf/{base}",
                                     headers={"User-Agent": "research-dump/0.1 (+https://github.com/hanslelee/research-dump)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
        # the reference list adds tokens but little signal
        cut = re.search(r"\n\s*(References|REFERENCES|Bibliography)\s*\n", text)
        if cut and cut.start() > len(text) * 0.4:
            text = text[:cut.start()]
        return re.sub(r"[ \t]+", " ", text)[:MAX_CHARS]
    except Exception as e:
        log(f"topics: PDF {aid} failed ({e}), using abstract")
        return ""


def pick_papers(papers, relations, paper_id):
    target = papers[paper_id]
    linked = set()
    for r in relations.values():
        if r.get("layer") == "0" and r.get("status") == "approved" and r.get("type") in ("extends", "conflicts"):
            if r["from"] == paper_id:
                linked.add(r["to"])
            elif r["to"] == paper_id:
                linked.add(r["from"])
    pool = [papers[i] for i in linked if i in papers]
    how = "linked"
    if not pool:
        pool = [q for q in papers.values() if q["id"] != paper_id and is_liked(q) and topic_set(q) & topic_set(target)]
        how = "liked-same-topic"
    # 좋아요 -> ★ -> newest (stable sorts: newest first, then by group)
    pool.sort(key=lambda q: str(q.get("published") or q.get("year") or ""), reverse=True)
    pool.sort(key=lambda q: (not is_liked(q), not is_starred(q)))
    return [target] + pool[:MAX_PAPERS - 1], how


def paper_block(p, text, focus):
    s = p.get("summary") or {}
    head = f"=== {'FOCUS ' if focus else ''}PAPER {p['id']} ===\nTitle: {clean_text(p.get('title', ''))}\nYear: {p.get('year') or ''}\n"
    if text:
        return head + "Full text:\n" + text
    return head + f"Abstract: {clean_text(p.get('abstract') or '')}\nSummary: {json.dumps(s, ensure_ascii=False)}"


def recommend(data_dir: Path, paper_id: str, today: str, log=print, chat=None, fetch=pdf_text):
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    llm_cfg = config.get("llm") or {}
    agent = llm_cfg.get("recommend") if (llm_cfg.get("recommend") or {}).get("model") not in (None, "", "SET-ME") else llm_cfg.get("summary")
    if not agent:
        raise llm.LLMUnavailable("no llm.recommend or llm.summary in config.json")
    language = (config.get("summary") or {}).get("language", "Korean, keeping technical terms in English")
    papers = load_dir(data_dir / "papers")
    if paper_id not in papers:
        raise ValueError(f"paper {paper_id} not found")
    chosen, how = pick_papers(papers, load_dir(data_dir / "relations"), paper_id)
    blocks, full = [], 0
    for i, p in enumerate(chosen):
        aid = arxiv_id(p)
        text = fetch(aid, log) if aid else ""
        full += bool(text)
        blocks.append(paper_block(p, text, i == 0))
    log(f"topics: {len(chosen)} papers ({how}), full text for {full}")
    system = SYSTEM.format(n=N_TOPICS, scope=config.get("scope", ""), language=language)
    reply = chat(agent, system, "\n\n".join(blocks), json_mode=True, timeout=300)
    data = llm.parse_json(reply["text"])
    rows = data.get("topics", []) if isinstance(data, dict) else data
    ids = {p["id"] for p in chosen}
    claims = []
    for t in [r for r in rows if isinstance(r, dict) and (r.get("text") or "").strip()][:N_TOPICS]:
        used = [i for i in (t.get("papers") or []) if i in ids] or [paper_id]
        cid = "claim:ai-" + hashlib.sha1(f"{paper_id}|{today}|{t['text']}".encode()).hexdigest()[:10]
        c = {"id": cid, "text": clean_text(t["text"]), "status": "candidate", "version": 1, "origin": "ai",
             "basis": clean_text(str(t.get("basis", ""))), "papers": used,
             "from_paper": paper_id, "memo": "", "history": [],
             "experiments": [{"title": "첫 실험", "status": "plan", "setup": clean_text(str(t.get("first_experiment", ""))), "result": ""}]
             if (t.get("first_experiment") or "").strip() else [],
             "created_at": today, "model": reply["model"], "used_full_text": full}
        write_json(data_dir / file_for("claims", cid), c)
        for pid in used:
            r = {"id": "rel:" + hashlib.sha1(f"{pid}|{cid}".encode()).hexdigest()[:12], "from": pid, "to": cid,
                 "layer": "0-1", "type": "motivates", "reason": "AI 연구 주제 추천에 쓰인 논문", "status": "new",
                 "model": reply["model"], "created_at": today, "source": "recommend"}
            write_json(data_dir / file_for("relations", r["id"]), r)
        claims.append(c)
    log(f"topics: {len(claims)} candidates from {paper_id}")
    return claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--paper", required=True)
    a = ap.parse_args()
    recommend(Path(a.data), a.paper, a.today)


if __name__ == "__main__":
    main()
