"""Collect candidate papers (no LLM).

    DATA_DIR=../research-dump-data python pipeline/collect.py
    python pipeline/collect.py --data ../research-dump-data
    python pipeline/collect.py --find-institution "Anthropic"   # print OpenAlex institution ids

Sources (each one is optional and skipped when its settings or key are missing):
  arxiv     new preprints matching config.collect.arxiv_categories x arxiv_keywords
  s2        new papers citing the papers you confirmed (needs S2_API_KEY)
  openalex  new papers by authors at config.collect.openalex_institutions (needs OPENALEX_API_KEY)

Writes candidates/<date>.json and state/collect.json. Never touches papers/.
Candidates already in papers/ or seen in an earlier run are dropped.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

USER_AGENT = "research-dump/0.1 (+https://github.com/hanslelee/research-dump)"
DEFAULT_COLLECT = {
    "days": 8,
    "arxiv_categories": ["cs.CL", "cs.LG", "cs.CV", "cs.AI", "cs.CR"],
    "arxiv_keywords": [],
    "s2_citations_of": "confirmed",
    "openalex_institutions": [],
    "openalex_keywords": [],
    "max_per_source": 400,
    "overlap_days": 3,
}


# ---------- http ----------
def http_get(url, headers=None, retries=3, pause=3.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(pause * (attempt + 1) * 2)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(pause * (attempt + 1))
                continue
            raise


def get_json(url, headers=None):
    return json.loads(http_get(url, headers))


# ---------- normalisation ----------
def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def title_key(t):
    return "t:" + hashlib.sha1(norm_title(t).encode()).hexdigest()[:16]


def arxiv_base(aid):
    return re.sub(r"v\d+$", "", aid.strip())


def make_item(*, title, abstract="", authors=(), year=None, venue="", url="", published="", ids=None, source=""):
    ids = {k: v for k, v in (ids or {}).items() if v}
    if ids.get("arxiv"):
        pid = "arxiv:" + arxiv_base(ids["arxiv"])
    elif ids.get("doi"):
        pid = "doi:" + ids["doi"].lower()
    elif ids.get("s2"):
        pid = "s2:" + ids["s2"]
    elif ids.get("openalex"):
        pid = "openalex:" + ids["openalex"]
    else:
        pid = title_key(title)
    return {
        "id": pid, "title": " ".join((title or "").split()), "abstract": " ".join((abstract or "").split()),
        "authors": list(authors), "year": year, "venue": venue or "", "url": url,
        "published": published, "ids": ids, "sources": [source] if source else [],
    }


# ---------- arXiv ----------
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_query(categories, keywords):
    cats = " OR ".join(f"cat:{c}" for c in categories)
    keys = " OR ".join(f'abs:"{k}"' if " " in k else f"abs:{k}" for k in keywords)
    return f"({cats}) AND ({keys})"


def parse_arxiv(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall("a:entry", ATOM):
        aid = e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1]
        published = e.findtext("a:published", "", ATOM)[:10]
        doi = e.findtext("arxiv:doi", "", ATOM)
        out.append(make_item(
            title=e.findtext("a:title", "", ATOM),
            abstract=e.findtext("a:summary", "", ATOM),
            authors=[a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
            year=int(published[:4]) if published else None,
            venue="arXiv", url=f"https://arxiv.org/abs/{arxiv_base(aid)}", published=published,
            ids={"arxiv": aid, "doi": doi}, source="arxiv",
        ))
    return out


def collect_arxiv(cfg, since, log):
    keywords = cfg["arxiv_keywords"]
    if not keywords:
        log("arxiv: skipped (no collect.arxiv_keywords)")
        return []
    q = arxiv_query(cfg["arxiv_categories"], keywords)
    items, start, page = [], 0, 100
    while start < cfg["max_per_source"]:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
            "search_query": q, "sortBy": "submittedDate", "sortOrder": "descending",
            "start": start, "max_results": page,
        })
        batch = parse_arxiv(http_get(url))
        fresh = [i for i in batch if i["published"] >= since]
        items += fresh
        if len(batch) < page or len(fresh) < len(batch):
            break
        start += page
        time.sleep(3)  # arXiv asks for 3 seconds between calls
    log(f"arxiv: {len(items)}")
    return items


# ---------- Semantic Scholar ----------
S2_FIELDS = "title,abstract,year,venue,externalIds,authors,publicationDate,url"


def s2_ref(paper):
    pid = paper["id"]
    if pid.startswith("arxiv:"):
        return "arXiv:" + pid[6:]
    if pid.startswith("doi:"):
        return "DOI:" + pid[4:]
    if pid.startswith("s2:"):
        return pid[3:]
    if paper.get("ids", {}).get("s2"):
        return paper["ids"]["s2"]
    return None


def s2_item(p, source):
    ext = p.get("externalIds") or {}
    return make_item(
        title=p.get("title"), abstract=p.get("abstract") or "",
        authors=[a.get("name", "") for a in p.get("authors") or []],
        year=p.get("year"), venue=p.get("venue") or "", url=p.get("url") or "",
        published=p.get("publicationDate") or (f"{p['year']}-01-01" if p.get("year") else ""),
        ids={"arxiv": ext.get("ArXiv"), "doi": ext.get("DOI"), "s2": p.get("paperId")}, source=source,
    )


def collect_s2(cfg, papers, since, log):
    key = os.environ.get("S2_API_KEY", "")
    if not key:
        log("s2: skipped (S2_API_KEY not set)")
        return []
    seeds = [p for p in papers.values() if cfg["s2_citations_of"] == "all" or p.get("status") == "confirmed"]
    items = []
    for p in seeds:
        ref = s2_ref(p)
        if not ref:
            continue
        url = f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(ref, safe=':')}/citations?" + urllib.parse.urlencode(
            {"fields": S2_FIELDS, "limit": 100})
        try:
            data = get_json(url, {"x-api-key": key})
        except urllib.error.HTTPError as e:
            log(f"s2: {ref} -> {e.code}")
            continue
        for row in data.get("data", []):
            it = s2_item(row.get("citingPaper") or {}, f"s2:cites:{p['id']}")
            if it["title"] and it["published"] >= since:
                items.append(it)
        time.sleep(1.1)  # 1 request per second
        if len(items) >= cfg["max_per_source"]:
            break
    log(f"s2: {len(items)} from {len(seeds)} confirmed papers")
    return items


# ---------- OpenAlex ----------
def openalex_abstract(inv):
    if not inv:
        return ""
    words = sorted((pos, w) for w, ps in inv.items() for pos in ps)
    return " ".join(w for _, w in words)


def openalex_item(w, source):
    ids = w.get("ids") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    arxiv = ""
    for loc in w.get("locations") or []:
        m = re.search(r"arxiv\.org/abs/([\w.]+)", (loc.get("landing_page_url") or ""))
        if m:
            arxiv = m.group(1)
            break
    return make_item(
        title=w.get("title") or w.get("display_name"), abstract=openalex_abstract(w.get("abstract_inverted_index")),
        authors=[(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []],
        year=w.get("publication_year"),
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
        url=(w.get("primary_location") or {}).get("landing_page_url") or w.get("id", ""),
        published=w.get("publication_date") or "",
        ids={"doi": doi, "arxiv": arxiv, "openalex": (ids.get("openalex") or w.get("id", "")).rsplit("/", 1)[-1]},
        source=source,
    )


def collect_openalex(cfg, since, log):
    key = os.environ.get("OPENALEX_API_KEY", "")
    inst = cfg["openalex_institutions"]
    if not inst:
        log("openalex: skipped (no collect.openalex_institutions)")
        return []
    if not key:
        log("openalex: skipped (OPENALEX_API_KEY not set)")
        return []
    keywords = cfg["openalex_keywords"] or cfg["arxiv_keywords"] or [""]
    lineage = "|".join(i["id"] if isinstance(i, dict) else i for i in inst)
    items = []
    for kw in keywords:
        params = {"filter": f"authorships.institutions.lineage:{lineage},from_publication_date:{since}",
                  "per-page": 100, "api_key": key}
        if kw:
            params["search"] = kw
        data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
        items += [openalex_item(w, "openalex") for w in data.get("results", [])]
        if len(items) >= cfg["max_per_source"]:
            break
    log(f"openalex: {len(items)}")
    return items


def find_institution(name):
    key = os.environ.get("OPENALEX_API_KEY", "")
    params = {"search": name, "per-page": 10}
    if key:
        params["api_key"] = key
    data = get_json("https://api.openalex.org/institutions?" + urllib.parse.urlencode(params))
    for r in data.get("results", []):
        print(f'{r["id"].rsplit("/", 1)[-1]}\t{r.get("display_name")}\t{r.get("country_code") or ""}\t{r.get("works_count")} works')


# ---------- merge ----------
def dedupe(items, known_ids, known_titles):
    merged = {}
    by_title = {}
    for it in items:
        tk = title_key(it["title"])
        if it["id"] in known_ids or tk in known_titles:
            continue
        key = it["id"] if it["id"] in merged else by_title.get(tk, it["id"])
        if key in merged:
            m = merged[key]
            m["sources"] = sorted(set(m["sources"]) | set(it["sources"]))
            m["ids"] = {**it["ids"], **m["ids"]}
            if not m["abstract"] and it["abstract"]:
                m["abstract"] = it["abstract"]
        else:
            merged[key] = it
            by_title[tk] = key
    return list(merged.values())


def load_dir(folder):
    return {d["id"]: d for d in (json.loads(p.read_text()) for p in sorted(folder.glob("*.json")))} if folder.exists() else {}


def run(data_dir: Path, today: str, log=print):
    config = json.loads((data_dir / "config.json").read_text())
    cfg = {**DEFAULT_COLLECT, **(config.get("collect") or {})}
    state_path = data_dir / "state" / "collect.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"last_run": None, "seen": []}
    default_since = (dt.date.fromisoformat(today) - dt.timedelta(days=cfg["days"])).isoformat()
    if state["last_run"]:
        # arXiv shows a paper a day or more after its "published" date, so look back a few days;
        # anything already proposed is dropped through state["seen"].
        since = (dt.date.fromisoformat(state["last_run"]) - dt.timedelta(days=cfg["overlap_days"])).isoformat()
    else:
        since = default_since

    papers = load_dir(data_dir / "papers")
    known_ids = set(papers) | set(state.get("seen", []))
    known_titles = {title_key(p["title"]) for p in papers.values()} | {s for s in state.get("seen", []) if s.startswith("t:")}

    log(f"collect since {since}")
    items = []
    for name, fn in (("arxiv", lambda: collect_arxiv(cfg, since, log)),
                     ("s2", lambda: collect_s2(cfg, papers, since, log)),
                     ("openalex", lambda: collect_openalex(cfg, since, log))):
        try:
            items += fn()
        except Exception as e:  # one source failing must not stop the others
            log(f"{name}: failed ({e})")
    items = dedupe(items, known_ids, known_titles)

    out = None
    if items:
        out = data_dir / "candidates" / f"{today}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(out.read_text())["items"] if out.exists() else []
        out.write_text(json.dumps({"collected_at": today, "since": since, "items": existing + items}, ensure_ascii=False, indent=2) + "\n")
    state["last_run"] = today
    state["seen"] = sorted(set(state.get("seen", [])) | {i["id"] for i in items} | {title_key(i["title"]) for i in items})
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    log(f"{len(items)} new candidates" + (f" -> {out.relative_to(data_dir)}" if out else ""))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--find-institution")
    a = ap.parse_args()
    if a.find_institution:
        find_institution(a.find_institution)
        return 0
    run(Path(a.data), a.today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
