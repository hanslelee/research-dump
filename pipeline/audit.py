"""Auditor: re-check a research topic's papers against their full text, with a different (stronger) model.

    python pipeline/audit.py --data ../research-dump-data --claim <id>

For each paper linked to the topic by an approved relation:
  1. summary   - does the abstract-based summary (claim / condition / gap) match the full text?
  2. relations - is each approved relation of this paper (to the topic, and extends/conflicts to the topic's
                 other papers) supported by the full text? right type?
Then, over all papers:
  3. claim action - keep / revise / narrow / duplicate_risk / retire / add_experiment, with the reason.

Flags only: nothing is changed. The report is stored on the claim as "audit" and shown on the web page.
The auditor model is config.json -> llm.audit, or llm.summary when unset. A different, stronger model is better
(otherwise the model grades its own work); the report notes when the same model was used.
Papers without an arXiv PDF are checked against the abstract and marked so.
Progress is saved after each paper; a run that runs out of time continues where it stopped.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
import llm  # noqa: E402
import topics  # noqa: E402
from common import clean_text, file_for, load_dir, write_json  # noqa: E402

REL_TYPES_01 = ("support", "narrow", "conflict", "duplicate", "motivates")

PAPER_SYSTEM = """You audit a research knowledge base for a researcher. Another model wrote a summary of a paper from its
abstract and proposed relations between papers and the researcher's research topic. Check them against the paper's TEXT.

1. summary: does the summary (claim / condition / gap) match what the paper actually shows?
   "verdict": "ok" or "issues". For issues, list each problem in one sentence and give a corrected summary.
2. relations: for each relation listed, is it supported by the paper's text?
   "verdict": "supported", "wrong_type" (a relation exists but of another type; give "suggested_type") or "unsupported".
   "evidence": a short quote or close paraphrase from the paper's text that decides it.
Relation types to the topic: support, narrow, conflict, duplicate, motivates. Between papers: extends, conflicts.
Be strict and concrete. Write in {language}.
Reply with JSON only:
{{"summary": {{"verdict": "ok", "issues": [], "corrected": {{"claim": "", "condition": "", "gap": ""}}}},
  "relations": [{{"id": "...", "verdict": "supported", "suggested_type": "", "evidence": "...", "note": ""}}]}}"""

ACTION_SYSTEM = """You audit a researcher's research topic after checking each linked paper against its full text.
Decide one action for the topic:
- "keep": the papers support the topic as written.
- "revise": the topic sentence should change (give "suggested_text").
- "narrow": it only holds under narrower conditions (give "suggested_text").
- "duplicate_risk": a paper already did (most of) it.
- "retire": the evidence goes against it.
- "add_experiment": it stands, but needs a specific experiment to be convincing (give "suggested_experiment").
Give "reason": two or three sentences naming the papers that decide it. Write in {language}.
Reply with JSON only: {{"action": "...", "reason": "...", "suggested_text": "", "suggested_experiment": ""}}"""


class OutOfTime(RuntimeError):
    """The run's time budget is used up; progress is saved and the next run continues."""


def audit_agent(config):
    """llm.audit, or llm.summary when unset. Returns (agent, same_as_summary)."""
    llm_cfg = config.get("llm") or {}
    agent = llm_cfg.get("audit")
    if not agent or agent.get("model") in (None, "", "SET-ME"):
        agent = llm_cfg.get("summary")
    if not agent:
        raise llm.LLMUnavailable("config.json에 llm.audit 또는 llm.summary가 없어요")
    return agent, agent.get("model") == (llm_cfg.get("summary") or {}).get("model")


def paper_relations(pid, claim_id, relations, linked):
    """Approved relations of this paper: to the claim, and extends/conflicts with the claim's other papers."""
    out = []
    for r in relations.values():
        if r.get("status") != "approved":
            continue
        if r.get("layer") == "0-1" and r.get("to") == claim_id and r.get("from") == pid:
            out.append(r)
        elif r.get("layer") == "0" and pid in (r.get("from"), r.get("to")) and {r.get("from"), r.get("to")} <= linked:
            out.append(r)
    return out


def run(data_dir: Path, claim_id: str, today: str, log=print, chat=None, fetch=topics.pdf_text, deadline=None):
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent, same = audit_agent(config)
    language = (config.get("summary") or {}).get("language", "Korean, keeping technical terms in English")
    claims = load_dir(data_dir / "claims")
    if claim_id not in claims:
        raise ValueError(f"research topic {claim_id} not found")
    claim = claims[claim_id]
    papers = load_dir(data_dir / "papers")
    relations = load_dir(data_dir / "relations")
    links = [r for r in relations.values() if r.get("layer") == "0-1" and r.get("to") == claim_id
             and r.get("status") == "approved" and r.get("from") in papers]
    linked = {r["from"] for r in links}
    if not linked:
        raise ValueError("승인된 관계로 연결된 논문이 없어요. 관련 논문의 관계를 먼저 승인해주세요.")

    # continue an unfinished audit, otherwise start a new one
    prev = claim.get("audit") or {}
    report = prev if prev.get("status") == "running" else {"status": "running", "started_at": today, "papers": {}}
    report["model"] = f"{agent['model']}"
    report["same_as_summary"] = same

    def save():
        claim["audit"] = report
        write_json(data_dir / file_for("claims", claim_id), claim)

    for pid in sorted(linked):
        if pid in report["papers"]:
            continue
        if deadline and time.time() > deadline:
            save()
            checkpoint.save(data_dir, f"Audit {claim_id}: {len(report['papers'])}/{len(linked)} papers", log)
            raise OutOfTime(f"{len(report['papers'])}/{len(linked)} papers audited")
        p = papers[pid]
        aid = topics.arxiv_id(p)
        text = fetch(aid, log) if aid else ""
        rels = paper_relations(pid, claim_id, relations, linked)
        other = lambda r: r["to"] if r["from"] == pid else r["from"]
        user = json.dumps({
            "research_topic": {"text": claim.get("text", ""), "basis": claim.get("basis", "")},
            "paper": {"id": pid, "title": clean_text(p.get("title", "")), "summary": p.get("summary") or {}},
            "text_source": "full text" if text else "abstract only",
            "text": text or clean_text(p.get("abstract") or ""),
            "relations": [{"id": r["id"], "type": r["type"], "reason": r.get("reason", ""),
                           "with": "the research topic" if r["layer"] == "0-1" else
                           {"title": (papers.get(other(r)) or {}).get("title", ""), "summary": (papers.get(other(r)) or {}).get("summary") or {},
                            "direction": "this paper -> other" if r["from"] == pid else "other -> this paper"}}
                          for r in rels],
        }, ensure_ascii=False)
        try:
            reply = chat(agent, PAPER_SYSTEM.format(language=language), user, json_mode=True, timeout=300)
            data = llm.parse_json(reply["text"])
        except llm.LLMUnavailable:
            save()
            raise
        except llm.LLMError as e:
            log(f"audit: {pid} skipped ({e})")
            report["papers"][pid] = {"source": "full" if text else "abstract", "error": str(e)[:200]}
            save()
            continue
        s = (data or {}).get("summary") or {}
        rel_ids = {r["id"] for r in rels}
        report["papers"][pid] = {
            "source": "full" if text else "abstract",
            "summary": {"verdict": "issues" if s.get("verdict") == "issues" else "ok",
                        "issues": [clean_text(str(x)) for x in s.get("issues") or []][:6],
                        "corrected": {k: clean_text(str((s.get("corrected") or {}).get(k, ""))) for k in ("claim", "condition", "gap")}
                        if s.get("verdict") == "issues" else {}},
            "relations": {r["id"]: {"verdict": r.get("verdict") if r.get("verdict") in ("supported", "wrong_type", "unsupported") else "supported",
                                    "suggested_type": r.get("suggested_type") or "", "evidence": clean_text(str(r.get("evidence", ""))),
                                    "note": clean_text(str(r.get("note", "")))}
                          for r in (data or {}).get("relations") or [] if isinstance(r, dict) and r.get("id") in rel_ids},
        }
        save()
        checkpoint.save(data_dir, f"Audit {claim_id}: {pid}", log)
        log(f"audit: {pid} ({'full text' if text else 'abstract'})")

    findings = {pid: {"title": papers[pid].get("title", ""), **v} for pid, v in report["papers"].items()}
    reply = chat(agent, ACTION_SYSTEM.format(language=language), json.dumps(
        {"research_topic": {"text": claim.get("text", ""), "basis": claim.get("basis", "")},
         "relations": [{"paper": papers[r["from"]].get("title", ""), "type": r["type"], "reason": r.get("reason", "")} for r in links],
         "audit": findings}, ensure_ascii=False), json_mode=True, timeout=300)
    a = llm.parse_json(reply["text"]) or {}
    action = a.get("action") if a.get("action") in ("keep", "revise", "narrow", "duplicate_risk", "retire", "add_experiment") else "keep"
    report["action"] = {"action": action, "reason": clean_text(str(a.get("reason", ""))),
                        "suggested_text": clean_text(str(a.get("suggested_text", ""))),
                        "suggested_experiment": clean_text(str(a.get("suggested_experiment", "")))}
    report["status"] = "done"
    report["finished_at"] = today
    save()
    log(f"audit {claim_id}: {len(report['papers'])} papers, action {action}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--claim", required=True)
    a = ap.parse_args()
    run(Path(a.data), a.claim, a.today)


if __name__ == "__main__":
    main()
