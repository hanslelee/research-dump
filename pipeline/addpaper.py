"""Add one paper by hand (arXiv id, arXiv link or title), then tag and summarize it right away.

    python pipeline/addpaper.py --data ../research-dump-data --query 2312.06681
    python pipeline/addpaper.py --data ../research-dump-data --query "Steering Llama 2 via Contrastive Activation Addition"

Used by the 추가하기 button on the web page (through requests_run.py).
If the paper is already in papers/, nothing is added and its id is returned.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402
import enrich  # noqa: E402
import screen  # noqa: E402
import summarize  # noqa: E402
from common import file_for, load_dir, new_marks, write_json  # noqa: E402

ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5})(v\d+)?", re.I)


def arxiv_id_in(query):
    m = ARXIV_RE.search(query.strip())
    return m.group(1) if m and (len(query.strip()) < 40 or "arxiv" in query.lower()) else ""


def lookup(query, log=print):
    """Return a candidate item (collect.make_item shape) or None."""
    aid = arxiv_id_in(query)
    if aid:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": aid, "max_results": 1})
        items = collect.parse_arxiv(collect.http_get(url))
        return items[0] if items and items[0]["title"] else None
    key = os.environ.get("S2_API_KEY", "")
    if key:
        url = "https://api.semanticscholar.org/graph/v1/paper/search/match?" + urllib.parse.urlencode(
            {"query": query, "fields": collect.S2_FIELDS})
        try:
            hit = (collect.get_json(url, {"x-api-key": key}).get("data") or [None])[0]
        except Exception as e:
            log(f"addpaper: Semantic Scholar search failed ({e})")
            hit = None
        if hit and enrich.same_title(hit.get("title"), query):
            return collect.s2_item(hit, "manual")
    time.sleep(1)
    words = " AND ".join(f"ti:{w}" for w in re.findall(r"[A-Za-z0-9]{3,}", query)[:12])
    if words:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": words, "max_results": 5})
        for it in collect.parse_arxiv(collect.http_get(url)):
            if enrich.same_title(it["title"], query):
                return it
    return None


def add(data_dir: Path, query: str, today: str, log=print, chat=None, find=lookup):
    papers = load_dir(data_dir / "papers")
    item = find(query, log)
    if not item:
        raise ValueError(f"논문을 찾지 못했어요: {query}")
    key = collect.norm_title(item["title"])
    for p in papers.values():
        if p["id"] == item["id"] or collect.norm_title(p.get("title")) == key:
            log(f"addpaper: already have {p['id']}")
            return p["id"], False
    paper = {
        "id": item["id"], "title": item["title"], "authors": [], "author_names": item.get("authors", []),
        "ids": item.get("ids", {}), "published": item.get("published", ""), "year": item.get("year"),
        "venue": item.get("venue", ""), "url": item.get("url", ""), "abstract": item.get("abstract", ""),
        "tags": {"method": ["other"], "main_method": "other", "goal": [], "modal": "text", "setting": "standalone", "topic": []},
        "summary": {"claim": "", "condition": "", "gap": ""},
        "marks": new_marks(), "memo": "", "entered_at": today, "source": "manual",
        "provenance": {"needs_tags": True, "added_by": "web", "query": query, "created_at": today},
    }
    write_json(data_dir / file_for("papers", paper["id"]), paper)
    log(f"addpaper: added {paper['id']} ({paper['title']})")
    screen.run(data_dir, today, log, chat=chat, only_retag=True)
    summarize.run(data_dir, today, log, chat=chat, only_ids={paper["id"]})
    return paper["id"], True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--query", required=True)
    a = ap.parse_args()
    print(add(Path(a.data), a.query, a.today))


if __name__ == "__main__":
    main()
