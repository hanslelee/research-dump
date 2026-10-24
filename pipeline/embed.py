"""Paper embeddings for fast candidate search (vector search before the model judges).

    python pipeline/embed.py --data ../research-dump-data      # embed papers that are new or changed

Stored in embeddings/papers.json: {"model": ..., "items": {paper id: {"h": text hash, "v": [768 floats]}}}.
Text = title + summary (or abstract when there is no summary). A paper is embedded again when that text changes.

Model: config.json -> llm.embed. When missing and llm.summary is a Gemini endpoint,
"gemini-embedding-001" on the same endpoint and key is used. Without a usable model every caller
falls back to comparing with all papers (slower, same result).
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import clean_text, load_dir, read_json, write_json  # noqa: E402

DIM = 768
TOP_K = 20
BATCH = 50
STORE = Path("embeddings") / "papers.json"


def embed_config(config):
    llm_cfg = config.get("llm") or {}
    if llm_cfg.get("embed"):
        return llm_cfg["embed"]
    s = llm_cfg.get("summary") or {}
    if "generativelanguage.googleapis.com" in (s.get("base_url") or ""):
        return {"base_url": s["base_url"], "key_name": s["key_name"], "model": "gemini-embedding-001"}
    return None


def paper_text(p):
    s = p.get("summary") or {}
    body = " ".join(x for x in (s.get("claim"), s.get("condition"), s.get("gap")) if x) or (p.get("abstract") or "")[:2000]
    topics = " ".join((p.get("tags") or {}).get("topic") or [])
    return clean_text(f"{p.get('title', '')}. {body} {topics}")


def claim_text(c):
    return clean_text(f"{c.get('text', '')}. {c.get('basis', '')}")


def normalize(v):
    v = list(v)[:DIM]  # gemini-embedding-001 allows cutting to the first dimensions
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 5) for x in v]


def http_embed(cfg, texts, retries=4):
    key = os.environ.get(cfg["key_name"], "")
    if not key:
        raise RuntimeError(f"Secret {cfg['key_name']} is not set")
    payload = {"model": cfg["model"], "input": texts, "dimensions": DIM}
    url = cfg["base_url"].rstrip("/") + "/embeddings"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            rows = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [normalize(d["embedding"]) for d in rows]
        except urllib.error.HTTPError as e:
            if e.code == 400 and "dimensions" in payload:  # endpoint without that option: cut the vector ourselves
                payload.pop("dimensions")
                continue
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"embedding {e.code}: {e.read()[:200]!r}") from e
    raise RuntimeError("embedding: no answer after retries")


def embedder(config):
    """Returns f(list of texts) -> list of vectors, or None when no embedding model is available."""
    cfg = embed_config(config)
    if not cfg or cfg.get("model") in (None, "", "SET-ME"):
        return None
    return lambda texts: http_embed(cfg, texts)


def ensure(data_dir: Path, config, log=print, embed_fn=None, papers=None):
    """Embed papers that are new or whose text changed. Returns {id: vector}, or None if embedding is not possible."""
    embed_fn = embed_fn or embedder(config)
    if not embed_fn:
        log("embed: no embedding model (set llm.embed in config.json)")
        return None
    papers = papers if papers is not None else load_dir(data_dir / "papers")
    store = read_json(data_dir / STORE, {"items": {}})
    items = {k: v for k, v in store.get("items", {}).items() if k in papers}
    todo = []
    for pid, p in papers.items():
        h = hashlib.sha1(paper_text(p).encode()).hexdigest()[:12]
        if items.get(pid, {}).get("h") != h:
            todo.append((pid, h, paper_text(p)))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            vecs = embed_fn([t for _, _, t in chunk])
        except Exception as e:
            log(f"embed: failed ({e})")
            if not items:
                return None
            break  # use what we have
        for (pid, h, _), v in zip(chunk, vecs):
            items[pid] = {"h": h, "v": normalize(v)}
    if todo:
        cfg = embed_config(config) or {}
        write_json(data_dir / STORE, {"model": cfg.get("model", "custom"), "dim": DIM, "items": items})
        log(f"embed: {len(todo)} papers embedded, {len(items)} stored")
    return {k: v["v"] for k, v in items.items()}


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def nearest(query_vec, vectors, candidates, k=TOP_K):
    """The k candidate ids closest to query_vec. Candidates without a vector are kept at the end."""
    scored = sorted(((cosine(query_vec, vectors[c]), c) for c in candidates if c in vectors), reverse=True)
    picked = [c for _, c in scored[:k]]
    missing = [c for c in candidates if c not in vectors]
    return picked + missing[:max(0, k - len(picked))]


def query(config, text, embed_fn=None):
    embed_fn = embed_fn or embedder(config)
    if not embed_fn:
        return None
    try:
        return normalize(embed_fn([text])[0])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    data_dir = Path(a.data)
    ensure(data_dir, json.loads((data_dir / "config.json").read_text()))


if __name__ == "__main__":
    main()
