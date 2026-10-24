"""Merge duplicate papers automatically (e.g. the arXiv version and the conference version of one paper).

    python pipeline/dedupe.py --data ../research-dump-data

Two papers are duplicates when their normalised titles match or they share an arXiv id / DOI.
The kept paper is the one with your marks or memo, then the arXiv one, then the one that entered first.
Marks are combined (liked if either was liked), memos are joined, missing summary/abstract filled in,
and relations pointing at the removed file are moved to the kept one.
"""
import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
from common import file_for, load_dir, new_marks, write_json  # noqa: E402


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def keys_of(p):
    ids = p.get("ids") or {}
    out = {"t:" + norm_title(p.get("title"))} if norm_title(p.get("title")) else set()
    arx = ids.get("arxiv") or (p["id"][6:] if p["id"].startswith("arxiv:") else "")
    if arx:
        out.add("a:" + re.sub(r"v\d+$", "", arx))
    doi = ids.get("doi") or (p["id"][4:] if p["id"].startswith("doi:") else "")
    if doi and not doi.lower().startswith("10.48550/arxiv"):
        out.add("d:" + doi.lower())
    return out


def groups(papers):
    parent = {pid: pid for pid in papers}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first = {}
    for pid, p in papers.items():
        for k in keys_of(p):
            if k in first:
                parent[find(pid)] = find(first[k])
            else:
                first[k] = pid
    out = {}
    for pid in papers:
        out.setdefault(find(pid), []).append(pid)
    return [g for g in out.values() if len(g) > 1]


def rank(p):
    m = p.get("marks") or {}
    human = any(m.get(k) for k in ("liked", "bookmarked")) or bool((p.get("memo") or "").strip())
    return (not human, not p["id"].startswith("arxiv:"), p.get("entered_at") or "9999", p["id"])


def merge(keep, other):
    km, om = {**new_marks(), **(keep.get("marks") or {})}, other.get("marks") or {}
    for k in ("liked", "bookmarked"):
        km[k] = bool(km.get(k) or om.get(k))
    km["hidden"] = bool(km.get("hidden") and om.get("hidden"))  # hidden only if both were hidden
    dates = [d for d in (km.get("liked_at"), om.get("liked_at")) if d]
    if km["liked"] and dates:
        km["liked_at"] = min(dates)
    keep["marks"] = km
    memos = [m.strip() for m in (keep.get("memo") or "", other.get("memo") or "") if m.strip()]
    keep["memo"] = "\n---\n".join(dict.fromkeys(memos))
    if not (keep.get("summary") or {}).get("claim") and (other.get("summary") or {}).get("claim"):
        keep["summary"] = other["summary"]
    if not keep.get("abstract") and other.get("abstract"):
        keep["abstract"] = other["abstract"]
    keep["ids"] = {**(other.get("ids") or {}), **(keep.get("ids") or {})}
    # the published version (non-arXiv venue) has the better venue and citation count
    if (other.get("venue") or "").lower() not in ("", "arxiv") and (keep.get("venue") or "").lower() in ("", "arxiv"):
        keep["venue"], keep["year"] = other["venue"], other.get("year") or keep.get("year")
    if ((other.get("meta") or {}).get("citations") or -1) > ((keep.get("meta") or {}).get("citations") or -1):
        keep["meta"] = other["meta"]
    tags = keep.setdefault("tags", {})
    tags["topic"] = sorted(set(tags.get("topic") or []) | set((other.get("tags") or {}).get("topic") or []))
    entered = [d for d in (keep.get("entered_at"), other.get("entered_at")) if d]
    if entered:
        keep["entered_at"] = min(entered)
    pv = keep.setdefault("provenance", {})
    pv["merged_from"] = sorted(set(pv.get("merged_from") or []) | {other["id"]})


def run(data_dir: Path, today: str, log=print) -> int:
    papers = load_dir(data_dir / "papers")
    relations = load_dir(data_dir / "relations")
    claims = load_dir(data_dir / "claims")
    moved = {}
    for g in groups(papers):
        ordered = sorted((papers[i] for i in g), key=rank)
        keep = ordered[0]
        for other in ordered[1:]:
            merge(keep, other)
            moved[other["id"]] = keep["id"]
            (data_dir / file_for("papers", other["id"])).unlink()
            log(f"dedupe: {other['id']} -> {keep['id']}")
        write_json(data_dir / file_for("papers", keep["id"]), keep)
    if not moved:
        log("dedupe: no duplicates")
        return 0

    seen = set()
    for r in sorted(relations.values(), key=lambda r: (r.get("status") != "approved", r["id"])):
        old = (r.get("from"), r.get("to"))
        r["from"], r["to"] = moved.get(r["from"], r["from"]), moved.get(r["to"], r["to"])
        key = (r["from"], r["to"], r.get("type"))
        path = data_dir / file_for("relations", r["id"])
        if r["from"] == r["to"] or key in seen:
            path.unlink()
            continue
        seen.add(key)
        if old != (r["from"], r["to"]):
            write_json(path, r)
    for c in claims.values():
        if any(p in moved for p in c.get("papers") or []):
            c["papers"] = list(dict.fromkeys(moved.get(p, p) for p in c["papers"]))
            write_json(data_dir / file_for("claims", c["id"]), c)
    checkpoint.save(data_dir, f"Dedupe: merged {len(moved)} papers", log)
    log(f"dedupe: merged {len(moved)} papers")
    return len(moved)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
