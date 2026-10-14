"""Screening agent: decide which candidates fit the research scope, tag them, and add them as papers.

    python pipeline/screen.py --data ../research-dump-data

Reads candidates/*.json, skips ids already screened (state/screen.json).
In-scope -> papers/<id>.json with status "new".
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
from common import GOAL, METHOD, MODAL, SETTING, clean_tags, file_for, load_dir, read_json, write_json  # noqa: E402

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
    if not pending:
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

    def save():
        state["done"] = sorted(done)
        write_json(state_path, state)
        if decisions:
            write_json(log_path, earlier + decisions)

    def ask(chunk):
        user = json.dumps([{"id": c["id"], "title": c["title"], "abstract": c["abstract"][:1500]} for c in chunk], ensure_ascii=False)
        reply = chat(agent, system, user, json_mode=True)
        data = llm.parse_json(reply["text"])
        rows = data.get("results", []) if isinstance(data, dict) else data
        return {r.get("id"): r for r in rows if isinstance(r, dict)}, reply["model"]

    def judge(chunk):
        """Ask for a batch; if the reply is unusable, split it and ask again (down to one paper)."""
        try:
            return [(c, *pair) for pair in [ask(chunk)] for c in chunk]
        except llm.LLMUnavailable:
            raise
        except llm.LLMError as e:
            if len(chunk) == 1:
                log(f"screening: skipped {chunk[0]['id']} ({e})")
                return []
            mid = len(chunk) // 2
            return judge(chunk[:mid]) + judge(chunk[mid:])

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
            if r.get("in_scope"):
                paper = {
                    "id": c["id"], "title": c["title"], "authors": [], "author_names": c.get("authors", []),
                    "ids": c.get("ids", {}), "published": c.get("published", ""),
                    "year": c.get("year"), "venue": c.get("venue", ""), "url": c.get("url", ""),
                    "abstract": c.get("abstract", ""), "tags": clean_tags(r.get("tags"), topics),
                    "summary": {"claim": "", "condition": "", "gap": ""},
                    "status": "new", "source": "collect",
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
