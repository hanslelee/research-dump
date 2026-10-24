"""Relation drafts between a research topic (claim) and papers. A person approves or rejects them.

    python pipeline/claimrel.py --data ../research-dump-data --claim <id>     # 관련 논문 찾기: claim vs all papers

Types (paper -> claim, layer "0-1"):
  support    the paper's finding backs the claim
  narrow     the paper shows the claim only holds under narrower conditions
  conflict   the paper's finding contradicts the claim
  duplicate  the paper already did (most of) this research topic
  motivates  the paper raises the question or provides the method the claim builds on
Strict: when unsure the answer is "none". Each relation keeps one evidence sentence in "reason".
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402
from compare import BATCH, agent_for, describe  # noqa: E402
from common import clean_text, file_for, load_dir, write_json  # noqa: E402

TYPES = ("support", "narrow", "conflict", "duplicate", "motivates")

SYSTEM = """You link papers to a researcher's own research topic (a claim or hypothesis they want to work on).
For each candidate paper decide how it relates to the RESEARCH TOPIC:
- "support": its finding backs the topic's hypothesis.
- "narrow": it shows the hypothesis holds only under narrower conditions.
- "conflict": its finding contradicts the hypothesis.
- "duplicate": it already did this research topic, or most of it.
- "motivates": it raises the question, or provides the method or finding the topic directly builds on.
- "none": anything else. Being on the same broad topic is "none".
Be strict. If you are not sure, answer "none".
For every answer other than "none" give "evidence": one sentence naming the specific finding of the paper and how it bears on the topic.
Write evidence in {language}.
Reply with JSON only: {{"relations": [{{"id": "<paper id>", "type": "...", "evidence": "..."}}]}}"""


def claim_text(c):
    out = {"topic": c.get("text", "")}
    if c.get("basis"):
        out["basis"] = c["basis"]
    return out


def rel_id(paper_id, claim_id):
    return "rel:" + hashlib.sha1(f"{paper_id}|{claim_id}|claim".encode()).hexdigest()[:12]


def link(data_dir: Path, claim_id: str, today: str, log=print, chat=None, paper_ids=None):
    """Compare one claim with papers (all papers, or only paper_ids). Pairs that already have a relation are skipped."""
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent = agent_for(config, log)
    if not agent:
        raise llm.LLMUnavailable("no llm.compare or llm.summary in config.json")
    language = (config.get("summary") or {}).get("language", "Korean, keeping technical terms in English")
    claims = load_dir(data_dir / "claims")
    if claim_id not in claims:
        raise ValueError(f"research topic {claim_id} not found")
    claim = claims[claim_id]
    papers = load_dir(data_dir / "papers")
    linked = {r["from"] for r in load_dir(data_dir / "relations").values() if r.get("layer") == "0-1" and r.get("to") == claim_id}
    cands = [p for p in papers.values() if p["id"] not in linked and (paper_ids is None or p["id"] in paper_ids)
             and (paper_ids is not None or not (p.get("marks") or {}).get("hidden"))]
    system = SYSTEM.format(language=language)
    written = []
    for i in range(0, len(cands), BATCH):
        chunk = cands[i:i + BATCH]
        user = json.dumps({"research_topic": claim_text(claim), "papers": [describe(p) for p in chunk]}, ensure_ascii=False)
        try:
            reply = chat(agent, system, user, json_mode=True)
            data = llm.parse_json(reply["text"])
        except llm.LLMUnavailable:
            raise
        except llm.LLMError as e:
            log(f"claimrel: skipped a batch ({e})")
            continue
        ids = {p["id"] for p in chunk}
        for r in (data.get("relations", []) if isinstance(data, dict) else data):
            if not isinstance(r, dict) or r.get("type") not in TYPES or r.get("id") not in ids or not (r.get("evidence") or "").strip():
                continue
            rel = {"id": rel_id(r["id"], claim_id), "from": r["id"], "to": claim_id, "layer": "0-1", "type": r["type"],
                   "reason": clean_text(r["evidence"]), "status": "new", "model": reply["model"], "created_at": today,
                   "source": "claim-search"}
            write_json(data_dir / file_for("relations", rel["id"]), rel)
            written.append(rel)
    log(f"claimrel {claim_id}: {len(written)} relations from {len(cands)} papers")
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--claim", required=True)
    a = ap.parse_args()
    link(Path(a.data), a.claim, a.today)


if __name__ == "__main__":
    main()
