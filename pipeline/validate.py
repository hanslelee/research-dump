"""Check a data folder against the schemas and cross-references.

    python pipeline/validate.py sample-data
    python pipeline/validate.py ../research-dump-data

Uses only the standard library: it checks required fields, allowed values and
that every id a file points at exists. It is not a full JSON Schema validator.
"""
import json
import sys
from pathlib import Path

METHOD = {"train", "inference", "eval", "analysis", "other"}
GOAL = {"harmlessness", "honesty", "helpfulness"}
MODAL = {"text", "multimodal"}
SETTING = {"standalone", "rag", "agent"}
L0 = {"extends", "conflicts"}
L01 = {"support", "narrow", "conflict", "duplicate", "motivates", "unrelated"}


def load(folder: Path, sub: str):
    return {d["id"]: d for d in (json.loads(p.read_text()) for p in sorted((folder / sub).glob("*.json")))}


def main(folder: Path) -> int:
    errors, warnings = [], []
    cfg = json.loads((folder / "config.json").read_text())
    topics = set(cfg.get("topics", []))
    papers, authors = load(folder, "papers"), load(folder, "authors")
    claims, relations = load(folder, "claims"), load(folder, "relations")

    def err(where, msg):
        errors.append(f"{where}: {msg}")

    for pid, p in papers.items():
        t = p.get("tags", {})
        if not set(t.get("method", [])) <= METHOD: err(pid, f"method {t.get('method')}")
        if t.get("main_method") and t["main_method"] not in t.get("method", []): err(pid, "main_method is not in method")
        if not set(t.get("goal", [])) <= GOAL: err(pid, f"goal {t.get('goal')}")
        if t.get("modal") not in MODAL: err(pid, f"modal {t.get('modal')}")
        if t.get("setting") not in SETTING: err(pid, f"setting {t.get('setting')}")
        extra = set(t.get("topic", [])) - topics
        if extra: warnings.append(f"{pid}: topic not in config.topics: {sorted(extra)}")
        if p.get("status") not in {"new", "hold", "confirmed"}: err(pid, f"status {p.get('status')}")
        for a in p.get("authors", []):
            if a not in authors: err(pid, f"unknown author {a}")

    for cid, c in claims.items():
        if c.get("status") not in {"candidate", "active", "hold", "retired"}: err(cid, f"status {c.get('status')}")

    for rid, r in relations.items():
        if r.get("from") not in papers: err(rid, f"unknown from {r.get('from')}")
        if r.get("layer") == "0":
            if r.get("type") not in L0: err(rid, f"layer 0 type {r.get('type')}")
            if r.get("to") not in papers: err(rid, f"unknown paper {r.get('to')}")
        elif r.get("layer") == "0-1":
            if r.get("type") not in L01: err(rid, f"layer 0-1 type {r.get('type')}")
            if r.get("to") not in claims: err(rid, f"unknown claim {r.get('to')}")
        else:
            err(rid, f"layer {r.get('layer')}")
        if not r.get("reason"): err(rid, "missing reason")
        if r.get("status") not in {"new", "approved", "rejected"}: err(rid, f"status {r.get('status')}")

    print(f"{len(papers)} papers, {len(authors)} authors, {len(claims)} claims, {len(relations)} relations")
    for w in warnings:
        print("WARN", w)
    for e in errors:
        print("ERROR", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "sample-data")))
