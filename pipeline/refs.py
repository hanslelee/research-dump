"""베이스라인 · 주요 참고 논문: the papers a paper cites, from Semantic Scholar.

    python pipeline/refs.py --data ../research-dump-data            # weekly: liked and ★ papers
    python pipeline/refs.py --data ../research-dump-data --paper <id>

Writes refs/<paper file>.json:
  {"paper": id, "fetched_at": date, "total": n, "items": [{id, title, year, venue_short, citations,
   influential, intents, url, ids, authors}]}
The web page shows the "key" ones first: cited as method (intents has "methodology") or marked influential,
most cited first, at most 10; the rest under 전체 참고문헌.
Weekly runs refresh files older than 30 days.
"""
import argparse
import datetime as dt
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402
import metadata  # noqa: E402
from common import file_for, is_liked, is_starred, load_dir, read_json, write_json  # noqa: E402

FIELDS = ("isInfluential,intents,citedPaper.paperId,citedPaper.title,citedPaper.year,citedPaper.venue,"
          "citedPaper.publicationVenue,citedPaper.citationCount,citedPaper.externalIds,citedPaper.url,"
          "citedPaper.authors,citedPaper.publicationDate")
REFRESH_DAYS = 30


def ref_item(row):
    p = row.get("citedPaper") or {}
    if not p.get("title"):
        return None
    it = collect.s2_item(p, "refs")
    return {"id": it["id"], "title": it["title"], "year": it.get("year"), "published": it.get("published", ""),
            "venue_short": metadata.short_venue(p), "citations": p.get("citationCount") or 0,
            "influential": bool(row.get("isInfluential")), "intents": row.get("intents") or [],
            "url": it.get("url") or "", "ids": it.get("ids") or {}, "authors": (it.get("authors") or [])[:6]}


def fetch(paper, log=print, get=None):
    """Return the refs record for one paper, or None when Semantic Scholar has nothing."""
    get = get or collect.get_json
    ref = metadata.s2_ref(paper)
    if not ref:
        return None
    key = os.environ.get("S2_API_KEY", "")
    url = (f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(ref, safe=':')}/references?"
           + urllib.parse.urlencode({"fields": FIELDS, "limit": 1000}))
    try:
        data = get(url, {"x-api-key": key} if key else {})
    except Exception as e:
        log(f"refs: {paper['id']} failed ({e})")
        return None
    items = [x for x in (ref_item(r) for r in data.get("data") or []) if x]
    items.sort(key=lambda x: -x["citations"])
    return {"paper": paper["id"], "total": len(items), "items": items}


def save(data_dir: Path, paper_id: str, rec: dict, today: str):
    rec = {"id": paper_id, **rec, "fetched_at": today}
    write_json(data_dir / file_for("refs", paper_id), rec)
    return rec


def run(data_dir: Path, today: str, log=print, get=None, pause=1.1):
    papers = load_dir(data_dir / "papers")
    old = (dt.date.fromisoformat(today) - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    done = 0
    for p in papers.values():
        if not (is_liked(p) or is_starred(p)):
            continue
        cur = read_json(data_dir / file_for("refs", p["id"]), None)
        if cur and cur.get("fetched_at", "") >= old:
            continue
        rec = fetch(p, log, get)
        if rec:
            save(data_dir, p["id"], rec, today)
            done += 1
        time.sleep(pause)
    log(f"refs: {done} papers updated")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--paper")
    a = ap.parse_args()
    data_dir = Path(a.data)
    if a.paper:
        rec = fetch(load_dir(data_dir / "papers")[a.paper])
        if rec:
            save(data_dir, a.paper, rec, a.today)
    else:
        run(data_dir, a.today)


if __name__ == "__main__":
    main()
