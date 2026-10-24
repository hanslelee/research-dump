"""관련 논문 웹 검색: find papers on the web (Semantic Scholar) for a research topic. Nothing is added to papers/.

    python pipeline/websearch.py --data ../research-dump-data --claim <id>

1. The model turns the topic and its 근거 into a few search queries.
2. Semantic Scholar relevance search for each query (any year).
3. Papers already in the repository are dropped; the model keeps only those relevant to the topic, with a reason.
4. Affiliations from OpenAlex (to mark top companies).
The result is stored on the claim as web_suggestions; the web page shows 10 of them, sorted by
좋은 학회, 인용수 or 탑 기업, each with an 추가하기 button.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402
import llm  # noqa: E402
import metadata  # noqa: E402
from common import clean_text, file_for, load_dir, write_json  # noqa: E402
from compare import agent_for  # noqa: E402

TOP_VENUES = {"NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "CVPR", "ICCV", "ECCV", "COLM", "TMLR", "TACL", "JMLR"}
TOP_ORGS = ["OpenAI", "Anthropic", "Google", "DeepMind", "Meta", "Microsoft", "Apple", "NVIDIA", "Amazon", "IBM",
            "ByteDance", "Alibaba", "Tencent", "Baidu", "Samsung", "NAVER", "LG AI", "Salesforce", "xAI", "Mistral"]
PER_QUERY = 25
KEEP = 30
FIELDS = "title,abstract,year,venue,publicationVenue,citationCount,externalIds,authors,url,publicationDate"

QUERY_SYSTEM = """You help a researcher search the literature for their research topic.
Write {n} short English search queries (3 to 6 words each) for an academic search engine that together find
the prior work this topic builds on, competes with, or might duplicate. Cover different angles (the method,
the phenomenon, the evaluation). Reply with JSON only: {{"queries": ["...", "..."]}}"""

FILTER_SYSTEM = """You check search results for a researcher's research topic.
For each paper decide "relevant": true only if the paper would belong in the related-work section of this topic
(prior work it builds on, a competing approach, a key finding it depends on, or the same idea already done).
Being in the same broad field is not enough.
Give "reason": one short sentence on how the paper relates to the topic, in {language}.
Reply with JSON only: {{"results": [{{"id": "...", "relevant": true, "reason": "..."}}]}}"""


def top_orgs(institutions):
    return [o for o in TOP_ORGS if any(o.lower() in (i or "").lower() for i in institutions)]


def s2_search(query, log):
    key = os.environ.get("S2_API_KEY", "")
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
        {"query": query, "limit": PER_QUERY, "fields": FIELDS})
    try:
        data = collect.get_json(url, {"x-api-key": key} if key else {})
    except Exception as e:
        log(f"websearch: '{query}' failed ({e})")
        return []
    out = []
    for row in data.get("data") or []:
        it = collect.s2_item(row, "web-search")
        if not it["title"]:
            continue
        it["venue_short"] = metadata.short_venue(row)
        it["citations"] = row.get("citationCount") or 0
        out.append(it)
    return out


def institutions_of(item, log):
    got = metadata.fetch_institutions({"id": item["id"], "ids": item.get("ids") or {}}, log)
    return got or {"institutions": [], "industry": False}


def search(data_dir: Path, claim_id: str, today: str, log=print, chat=None, search_fn=s2_search, inst_fn=institutions_of,
           pause=1.1):
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
    topic = {"topic": claim.get("text", ""), "basis": claim.get("basis", "")}

    reply = chat(agent, QUERY_SYSTEM.format(n=4), json.dumps(topic, ensure_ascii=False), json_mode=True)
    queries = [q for q in (llm.parse_json(reply["text"]) or {}).get("queries", []) if isinstance(q, str) and q.strip()][:5]
    if not queries:
        raise llm.LLMError("no search queries")
    log(f"websearch: queries {queries}")

    papers = load_dir(data_dir / "papers")
    have_ids = set(papers) | {"arxiv:" + collect.arxiv_base(a) for p in papers.values() if (a := (p.get("ids") or {}).get("arxiv"))}
    have_titles = {collect.norm_title(p.get("title")) for p in papers.values()}
    found = {}
    for q in queries:
        for it in search_fn(q, log):
            if it["id"] in have_ids or collect.norm_title(it["title"]) in have_titles:
                continue
            found.setdefault(it["id"], it)
        time.sleep(pause)
    cands = list(found.values())
    log(f"websearch: {len(cands)} papers not in the repository")

    kept = []
    for i in range(0, len(cands), 10):
        chunk = cands[i:i + 10]
        user = json.dumps({"research_topic": topic, "papers": [
            {"id": c["id"], "title": clean_text(c["title"]), "year": c.get("year"), "abstract": clean_text(c.get("abstract") or "")[:800]}
            for c in chunk]}, ensure_ascii=False)
        try:
            r = chat(agent, FILTER_SYSTEM.format(language=language), user, json_mode=True)
            rows = (llm.parse_json(r["text"]) or {}).get("results", [])
        except llm.LLMUnavailable:
            raise
        except llm.LLMError as e:
            log(f"websearch: skipped a batch ({e})")
            continue
        verdict = {x.get("id"): x for x in rows if isinstance(x, dict)}
        for c in chunk:
            v = verdict.get(c["id"]) or {}
            if v.get("relevant"):
                kept.append({**c, "reason": clean_text(v.get("reason", ""))})
    kept.sort(key=lambda c: -c["citations"])
    kept = kept[:KEEP]
    for c in kept:
        inst = inst_fn(c, log)
        c["institutions"] = inst["institutions"]
        c["industry"] = inst["industry"]
        c["top_orgs"] = top_orgs(inst["institutions"])
        c["top_venue"] = c.get("venue_short") in TOP_VENUES
        c.pop("sources", None)
    claim["web_suggestions"] = {"searched_at": today, "queries": queries, "model": reply["model"], "items": kept}
    write_json(data_dir / file_for("claims", claim_id), claim)
    log(f"websearch {claim_id}: {len(kept)} suggestions")
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--claim", required=True)
    a = ap.parse_args()
    search(Path(a.data), a.claim, a.today)


if __name__ == "__main__":
    main()
