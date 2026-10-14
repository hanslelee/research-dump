"""Venue, date, citations, affiliations and popularity for every paper.

    python pipeline/metadata.py --data ../research-dump-data

Semantic Scholar (batch, needs S2_API_KEY): venue, publication date, citation count. Refreshed every run.
OpenAlex (one lookup per paper, once): author institutions and whether any is a company.
Popularity: citations per month since publication, ranked within your own papers.
  top `star2` share -> "★★", top `star` share -> "★", younger than `new_weeks` -> "new".

Writes paper["meta"]. Never changes the paper's tags, summary or status.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
import collect  # noqa: E402
from common import file_for, load_dir, write_json  # noqa: E402

S2_BATCH_FIELDS = "venue,publicationVenue,year,publicationDate,citationCount,influentialCitationCount,externalIds"
SHORT_VENUES = ["NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "EACL", "COLING", "COLM", "AAAI", "IJCAI",
                "CVPR", "ICCV", "ECCV", "TMLR", "JMLR", "TACL", "KDD", "WWW", "FAccT", "USENIX Security", "CCS",
                "S&P", "INLG", "AISTATS", "UAI", "SaTML"]
ORG_ALIASES = {
    "National University of Singapore": "NUS", "Carnegie Mellon University": "CMU",
    "Massachusetts Institute of Technology": "MIT", "University of California, Berkeley": "UC Berkeley",
    "Stanford University": "Stanford", "Korea Advanced Institute of Science and Technology": "KAIST",
    "Seoul National University": "SNU", "Tsinghua University": "Tsinghua", "Peking University": "PKU",
    "Nanyang Technological University": "NTU", "University of Oxford": "Oxford", "University of Cambridge": "Cambridge",
    "Princeton University": "Princeton", "Harvard University": "Harvard", "University of Washington": "UW",
    "Microsoft Research": "Microsoft", "Google DeepMind": "Google DeepMind", "DeepMind": "Google DeepMind",
}


# ---------- Semantic Scholar ----------
def s2_ref(p):
    pid, ids = p["id"], p.get("ids") or {}
    if ids.get("s2"):
        return ids["s2"]
    if pid.startswith("arxiv:"):
        return "ARXIV:" + pid[6:]
    if pid.startswith("doi:"):
        return "DOI:" + pid[4:]
    if pid.startswith("acl:"):
        return "ACL:" + pid[4:]
    if pid.startswith("s2:"):
        return pid[3:]
    if ids.get("arxiv"):
        return "ARXIV:" + collect.arxiv_base(ids["arxiv"])
    if ids.get("doi"):
        return "DOI:" + ids["doi"]
    return None


def http_post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"User-Agent": collect.USER_AGENT, "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def short_venue(row):
    pv = row.get("publicationVenue") or {}
    names = [pv.get("name") or "", *(pv.get("alternate_names") or []), row.get("venue") or ""]
    for short in SHORT_VENUES:
        for n in names:
            if re.search(rf"(^|[^A-Za-z]){re.escape(short)}([^A-Za-z]|$)", n):
                return short
    v = row.get("venue") or pv.get("name") or ""
    if not v or "arxiv" in v.lower():
        return "arXiv"
    return v if len(v) <= 40 else v[:38] + "…"


def fetch_s2(papers, log):
    key = os.environ.get("S2_API_KEY", "")
    if not key:
        log("metadata: Semantic Scholar skipped (S2_API_KEY not set)")
        return {}
    refs = {p["id"]: s2_ref(p) for p in papers}
    refs = {pid: r for pid, r in refs.items() if r}
    out = {}
    items = list(refs.items())
    for i in range(0, len(items), 400):
        chunk = items[i:i + 400]
        try:
            rows = http_post_json(f"https://api.semanticscholar.org/graph/v1/paper/batch?fields={S2_BATCH_FIELDS}",
                                  {"ids": [r for _, r in chunk]}, {"x-api-key": key})
        except Exception as e:
            log(f"metadata: Semantic Scholar batch failed ({e})")
            continue
        for (pid, _), row in zip(chunk, rows):
            if row:
                out[pid] = row
        time.sleep(1.1)
    return out


# ---------- OpenAlex ----------
def openalex_ref(p):
    pid, ids = p["id"], p.get("ids") or {}
    if pid.startswith("arxiv:") or ids.get("arxiv"):
        aid = pid[6:] if pid.startswith("arxiv:") else collect.arxiv_base(ids["arxiv"])
        return f"doi:10.48550/arXiv.{aid}"
    if pid.startswith("doi:"):
        return "doi:" + pid[4:]
    if ids.get("doi"):
        return "doi:" + ids["doi"]
    return None


def short_org(name):
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()
    return ORG_ALIASES.get(name, name)


def fetch_institutions(p, log):
    key = os.environ.get("OPENALEX_API_KEY", "")
    ref = openalex_ref(p)
    if not ref:
        return None
    url = f"https://api.openalex.org/works/{urllib.parse.quote(ref, safe=':/')}"
    if key:
        url += "?" + urllib.parse.urlencode({"api_key": key})
    try:
        w = collect.get_json(url)
    except Exception as e:
        log(f"metadata: OpenAlex {p['id']} ({e})")
        return None
    names, industry = [], False
    for a in w.get("authorships") or []:
        for inst in a.get("institutions") or []:
            n = short_org(inst.get("display_name"))
            if n and n not in names:
                names.append(n)
            if inst.get("type") == "company":
                industry = True
    return {"institutions": names, "industry": industry}


# ---------- popularity ----------
def months_since(date_str, today):
    try:
        d = dt.date.fromisoformat(date_str[:10]) if len(date_str) >= 10 else dt.date(int(date_str[:4]), 7, 1)
    except (ValueError, TypeError):
        return None
    return max((today - d).days / 30.44, 0.0)


def rank_popularity(papers, today, star=0.2, star2=0.05, new_weeks=6):
    """Set meta.popularity on each paper: "★★", "★", "new" or ""."""
    rated = []
    for p in papers:
        m = p.setdefault("meta", {})
        age = months_since(m.get("published") or p.get("published") or str(p.get("year") or ""), today)
        cites = m.get("citations")
        if age is None or cites is None:
            m["popularity"] = ""
            continue
        if age * 30.44 < new_weeks * 7:
            m["popularity"] = "new"
            continue
        m["cites_per_month"] = round(cites / max(age, 1.0), 2)
        rated.append(p)
    rated.sort(key=lambda p: p["meta"]["cites_per_month"], reverse=True)
    n = len(rated)
    for i, p in enumerate(rated):
        share = (i + 1) / n
        cpm = p["meta"]["cites_per_month"]
        p["meta"]["popularity"] = "★★" if cpm > 0 and share <= star2 else "★" if cpm > 0 and share <= star else ""
    return papers


def run(data_dir: Path, today: str, log=print):
    config = json.loads((data_dir / "config.json").read_text())
    pop = config.get("popularity") or {}
    papers = list(load_dir(data_dir / "papers").values())
    if not papers:
        log("metadata: no papers")
        return 0
    s2 = fetch_s2(papers, log)
    looked_up = 0
    for p in papers:
        m = p.setdefault("meta", {})
        row = s2.get(p["id"])
        if row:
            m["venue"] = short_venue(row)
            m["year"] = row.get("year") or p.get("year")
            m["published"] = row.get("publicationDate") or m.get("published") or ""
            m["citations"] = row.get("citationCount")
            m["influential_citations"] = row.get("influentialCitationCount")
            if row.get("paperId"):
                p.setdefault("ids", {})["s2"] = row["paperId"]
        else:
            m.setdefault("venue", p.get("venue") or "")
            m.setdefault("year", p.get("year"))
        if "institutions" not in m:
            inst = fetch_institutions(p, log)
            if inst is not None:
                m.update(inst)
                looked_up += 1
            time.sleep(0.2)
        m["updated_at"] = today
    rank_popularity(papers, dt.date.fromisoformat(today), float(pop.get("star", 0.2)),
                    float(pop.get("star2", 0.05)), int(pop.get("new_weeks", 6)))
    for p in papers:
        write_json(data_dir / file_for("papers", p["id"]), p)
    stars = sum(1 for p in papers if p["meta"].get("popularity", "").startswith("★"))
    log(f"metadata: {len(s2)}/{len(papers)} from Semantic Scholar, {looked_up} affiliations looked up, {stars} starred")
    checkpoint.save(data_dir, "Metadata: venue, citations, affiliations", log)
    return len(papers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
