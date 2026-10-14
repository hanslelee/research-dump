"""Fill in missing abstracts (and author names) for papers, so they can be summarized.

    python pipeline/enrich.py --data ../research-dump-data

arXiv ids: arXiv API (no key). Others: Semantic Scholar title search (needs S2_API_KEY).
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402
from common import file_for, load_dir, write_json  # noqa: E402


def same_title(a, b):
    """Exact, prefix (our title is a shortened form) or high word overlap."""
    a, b = collect.norm_title(a), collect.norm_title(b)
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / len(wa | wb) >= 0.8


def run(data_dir: Path, log=print, max_papers: int = 200):
    papers = load_dir(data_dir / "papers")
    need = [p for p in papers.values() if not (p.get("abstract") or "").strip()][:max_papers]
    if not need:
        log("enrich: nothing to do")
        return 0
    filled = 0

    arxiv = [p for p in need if p["id"].startswith("arxiv:")]
    for i in range(0, len(arxiv), 50):
        chunk = arxiv[i:i + 50]
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"id_list": ",".join(p["id"][6:] for p in chunk), "max_results": len(chunk)})
        try:
            found = {it["id"]: it for it in collect.parse_arxiv(collect.http_get(url))}
        except Exception as e:
            log(f"enrich: arXiv failed ({e})")
            break
        for p in chunk:
            it = found.get(p["id"])
            if it and it["abstract"]:
                p["abstract"] = it["abstract"]
                if not p.get("author_names"):
                    p["author_names"] = it["authors"]
                write_json(data_dir / file_for("papers", p["id"]), p)
                filled += 1
        time.sleep(3)

    key = os.environ.get("S2_API_KEY", "")
    others = [p for p in need if not p["id"].startswith("arxiv:")]
    if others and not key:
        log(f"enrich: {len(others)} non-arXiv papers skipped (S2_API_KEY not set)")
    for p in others if key else []:
        url = "https://api.semanticscholar.org/graph/v1/paper/search/match?" + urllib.parse.urlencode(
            {"query": p["title"], "fields": "title,abstract,authors"})
        try:
            data = collect.get_json(url, {"x-api-key": key}).get("data") or []
        except Exception as e:
            log(f"enrich: S2 {p['id']} ({e})")
            continue
        hit = data[0] if data else None
        if hit and hit.get("abstract") and same_title(hit.get("title"), p["title"]):
            p["abstract"] = hit["abstract"]
            if not p.get("author_names"):
                p["author_names"] = [a.get("name", "") for a in hit.get("authors") or []]
            write_json(data_dir / file_for("papers", p["id"]), p)
            filled += 1
        time.sleep(1.1)

    log(f"enrich: abstracts filled for {filled}/{len(need)} papers")
    return filled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    run(Path(ap.parse_args().data))


if __name__ == "__main__":
    main()
