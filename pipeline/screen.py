"""Screening agent: decide which candidates fit the research scope, tag them, and add them as papers.

    python pipeline/screen.py --data ../research-dump-data

Reads candidates/*.json, skips ids already screened (state/screen.json).
In-scope -> papers/<id>.json (nothing marked yet: 좋아요/북마크/숨김 are set by a person).
Keyword statistics (collected and included per keyword) -> state/keywords.json.
Every decision (in or out, with reason and model) -> logs/screening/<date>.json,
so recall can be measured later against a frozen set.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
import llm  # noqa: E402
from common import (GOAL, METHOD, MODAL, SETTING, clean_tags, clean_text, file_for, load_dir, matched_keywords,  # noqa: E402
                    new_marks, read_json, write_json)  # noqa: E402

SYSTEM = """You screen new research papers for a researcher.
Research scope: {scope}
{rules}
For each paper decide if it is IN SCOPE: its main contribution must be about the scope, not just mention it.
Be inclusive for borderline papers that a researcher in this scope would want to know about.
Out-of-scope rules win over in-scope ones.
For in-scope papers, also assign tags:
- method (list, 1+): {method}. train = proposes a training method; inference = inference-time method (steering, decoding, guardrail at run time, prompting); eval = benchmark or evaluation protocol; analysis = explains a phenomenon; other = attacks or anything else.
- main_method: the one main contribution, from method.
- goal (list): {goal}.
- modal: {modal}.
- setting: {setting}.
- topic (list, 0+): only from {topics}.

Reply with JSON only:
{{"results": [{{"id": "...", "in_scope": true, "reason": "one short sentence", "tags": {{...}}}}]}}
Omit "tags" when in_scope is false."""


def batches(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def run(data_dir: Path, today: str, log=print, chat=None):
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent = (config.get("llm") or {}).get("screening")
    if not agent:
        log("screening: skipped (no llm.screening in config.json)")
        return []
    opts = config.get("screening") or {}
    batch_size = int(opts.get("batch_size", 10))
    max_items = int(opts.get("max_per_run", 400))
    topics = config.get("topics", [])

    state_path = data_dir / "state" / "screen.json"
    state = read_json(state_path, {"done": []})
    done = set(state["done"])
    papers = load_dir(data_dir / "papers")

    pending = []
    for f in sorted((data_dir / "candidates").glob("*.json")) if (data_dir / "candidates").exists() else []:
        for it in json.loads(f.read_text())["items"]:
            if it["id"] not in done and it["id"] not in papers:
                pending.append(it)
    pending = pending[:max_items]
    # papers a person included from the 제외됨 list still need tags
    retag = [p for p in papers.values() if (p.get("provenance") or {}).get("needs_tags")]
    if not pending and not retag:
        log("screening: nothing to screen")
        return []

    rules = ""
    if config.get("scope_include"):
        rules += "In scope:\n" + "\n".join(f"- {r}" for r in config["scope_include"]) + "\n"
    if config.get("scope_exclude"):
        rules += "Out of scope:\n" + "\n".join(f"- {r}" for r in config["scope_exclude"]) + "\n"
    system = SYSTEM.format(scope=config.get("scope", ""), rules=rules, method=", ".join(METHOD), goal=", ".join(GOAL),
                           modal=" or ".join(MODAL), setting=" or ".join(SETTING), topics=", ".join(topics))
    decisions, added = [], []
    log_path = data_dir / "logs" / "screening" / f"{today}.json"
    earlier = read_json(log_path, [])
    keywords = (config.get("collect") or {}).get("arxiv_keywords") or []
    kw_path = data_dir / "state" / "keywords.json"
    kw = read_json(kw_path, {"last_run_date": "", "last_run": {}, "total": {}})
    if kw.get("last_run_date") != today:
        kw["last_run_date"], kw["last_run"] = today, {}

    def count_keywords(c, included):
        for k in matched_keywords(f"{c.get('title', '')} {c.get('abstract', '')}", keywords):
            for bucket in (kw["last_run"], kw["total"]):
                row = bucket.setdefault(k, {"collected": 0, "included": 0})
                row["collected"] += 1
                row["included"] += int(included)

    def save():
        state["done"] = sorted(done)
        write_json(state_path, state)
        if decisions:
            write_json(log_path, earlier + decisions)
        if keywords:
            write_json(kw_path, kw)

    def ask(chunk, sys_prompt=system):
        user = json.dumps([{"id": c["id"], "title": clean_text(c["title"]), "abstract": clean_text(c.get("abstract") or "")[:1500]} for c in chunk], ensure_ascii=False)
        reply = chat(agent, sys_prompt, user, json_mode=True)
        data = llm.parse_json(reply["text"])
        rows = data.get("results", []) if isinstance(data, dict) else data
        return {r.get("id"): r for r in rows if isinstance(r, dict)}, reply["model"]

    def judge(chunk, sys_prompt=system):
        """Ask for a batch; if the reply is unusable, split it and ask again (down to one paper)."""
        try:
            return [(c, *pair) for pair in [ask(chunk, sys_prompt)] for c in chunk]
        except llm.LLMUnavailable:
            raise
        except llm.LLMError as e:
            if len(chunk) == 1:
                log(f"screening: skipped {chunk[0]['id']} ({e})")
                return []
            mid = len(chunk) // 2
            return judge(chunk[:mid], sys_prompt) + judge(chunk[mid:], sys_prompt)

    tag_only = system + "\nThe researcher already chose to keep these papers: set in_scope to true and always give tags."
    for chunk in batches(retag, batch_size):
        try:
            judged = judge(chunk, tag_only)
        except llm.LLMUnavailable as e:
            log(f"screening: tagging stopped ({e})")
            break
        for p, results, model in judged:
            r = results.get(p["id"]) or {}
            if r.get("tags"):
                p["tags"] = clean_tags(r["tags"], topics)
                p["provenance"].pop("needs_tags", None)
                p["provenance"]["tag_model"] = model
                write_json(data_dir / file_for("papers", p["id"]), p)
                log(f"screening: tagged {p['id']} (included by you)")

    for chunk in batches(pending, batch_size):
        try:
            judged = judge(chunk)
        except llm.LLMUnavailable as e:
            log(f"screening: stopped ({e})")
            break
        for c, results, model in judged:
            r = results.get(c["id"])
            if r is None:
                continue  # not answered: try again next run
            done.add(c["id"])
            decisions.append({"id": c["id"], "title": c["title"], "in_scope": bool(r.get("in_scope")),
                              "reason": r.get("reason", ""), "model": model, "sources": c.get("sources", [])})
            count_keywords(c, bool(r.get("in_scope")))
            if r.get("in_scope"):
                paper = {
                    "id": c["id"], "title": c["title"], "authors": [], "author_names": c.get("authors", []),
                    "ids": c.get("ids", {}), "published": c.get("published", ""),
                    "year": c.get("year"), "venue": c.get("venue", ""), "url": c.get("url", ""),
                    "abstract": c.get("abstract", ""), "tags": clean_tags(r.get("tags"), topics),
                    "summary": {"claim": "", "condition": "", "gap": ""},
                    "marks": new_marks(), "memo": "", "entered_at": today, "source": "collect",
                    "provenance": {"screen_model": model, "screen_reason": r.get("reason", ""),
                                   "found_via": c.get("sources", []), "created_at": today},
                }
                write_json(data_dir / file_for("papers", paper["id"]), paper)
                added.append(paper)
        save()  # after every batch, so a stopped run keeps its progress
        checkpoint.save(data_dir, f"Screening: {len(decisions)} judged, {len(added)} in scope", log)

    save()
    log(f"screening: {len(decisions)} judged, {len(added)} in scope")
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
