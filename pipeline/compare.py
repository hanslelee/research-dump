"""Compare agent: propose extends / conflicts relations between papers. A person approves or rejects them.

    python pipeline/compare.py --data ../research-dump-data                 # weekly run
    python pipeline/compare.py --data ../research-dump-data --paper <id>    # 관계 찾기 for one paper

Weekly run (after the summary agent). Key papers = papers you liked + papers linked to a research topic.
  - papers that entered in the last 7 days  vs  key papers
  - papers that became key since the last run (liked or newly linked)  vs  all papers
관계 찾기 (one paper): that paper vs every other paper.
In every case only the 20 closest papers by embedding (embed.py) are sent to the model.
Without an embedding model: weekly pairs must share a topic tag, 관계 찾기 compares with all papers.

Strict: when unsure the answer is "none". No limit on how many relations; each keeps one evidence sentence
in "reason". New relations get status "new" and wait for 승인/거절 on the web page.
Pairs already compared are remembered in state/compare.json and not asked again in weekly runs.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
import embed  # noqa: E402
import llm  # noqa: E402
from common import clean_text, file_for, is_liked, load_dir, read_json, topic_set, write_json  # noqa: E402

SYSTEM = """You compare research papers for a researcher looking for research gaps.
You get one TARGET paper and several CANDIDATE papers. For each candidate decide its relation to the target:
- "extends": one paper builds directly on the other: it fixes, generalises, tests, or fills a gap left open by
  the other's specific method or finding.
- "conflicts": the two papers report findings or claims that contradict each other under comparable conditions.
- "none": anything else. Being on the same topic, using similar methods, or citing the same work is "none".
Be strict. If you are not sure, answer "none".
For "extends" also give "direction": "target_extends" if the TARGET builds on the candidate,
"candidate_extends" if the candidate builds on the TARGET.
For "extends" and "conflicts" give "evidence": one sentence naming the specific claim of each paper that shows it.
Write evidence in {language}.
Reply with JSON only:
{{"relations": [{{"id": "<candidate id>", "type": "extends|conflicts|none", "direction": "...", "evidence": "..."}}]}}"""

BATCH = 8


def agent_for(config, log=print):
    llm_cfg = config.get("llm") or {}
    agent = llm_cfg.get("compare")
    if not agent or agent.get("model") in (None, "", "SET-ME"):
        agent = llm_cfg.get("summary")
        if agent:
            log("compare: llm.compare not set, using llm.summary")
    return agent


def describe(p):
    s = p.get("summary") or {}
    row = {"id": p["id"], "title": clean_text(p.get("title", "")), "year": p.get("year")}
    if s.get("claim"):
        row.update(claim=s["claim"], condition=s.get("condition", ""), gap=s.get("gap", ""))
    else:
        row["abstract"] = clean_text(p.get("abstract") or "")[:1200]
    return row


def pair_key(a, b):
    return "|".join(sorted((a, b)))


def rel_id(src, dst, typ):
    return "rel:" + hashlib.sha1(f"{src}|{dst}|{typ}".encode()).hexdigest()[:12]


def existing_pairs(relations):
    return {pair_key(r["from"], r["to"]) for r in relations.values() if r.get("layer") == "0"}


def ask(agent, chat, language, target, cands):
    user = json.dumps({"target": describe(target), "candidates": [describe(c) for c in cands]}, ensure_ascii=False)
    reply = chat(agent, SYSTEM.format(language=language), user, json_mode=True)
    data = llm.parse_json(reply["text"])
    rows = data.get("relations", []) if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict)], reply["model"]


def to_relations(target, cands, rows, model, today):
    by_id = {c["id"]: c for c in cands}
    out = []
    for r in rows:
        typ, cid = r.get("type"), r.get("id")
        if typ not in ("extends", "conflicts") or cid not in by_id or not (r.get("evidence") or "").strip():
            continue
        src, dst = (cid, target["id"]) if typ == "extends" and r.get("direction") == "candidate_extends" else (target["id"], cid)
        out.append({"id": rel_id(src, dst, typ), "from": src, "to": dst, "layer": "0", "type": typ,
                    "reason": clean_text(r["evidence"]), "status": "new", "model": model, "created_at": today,
                    "source": "compare"})
    return out


def compare_target(data_dir, agent, chat, language, target, cands, today, log):
    """Returns (relations written, calls used). Raises LLMUnavailable to stop the run."""
    written, calls = [], 0
    for i in range(0, len(cands), BATCH):
        chunk = cands[i:i + BATCH]
        try:
            rows, model = ask(agent, chat, language, target, chunk)
        except llm.LLMError as e:
            log(f"compare: skipped {target['id']} batch ({e})")
            calls += 1
            continue
        calls += 1
        for rel in to_relations(target, chunk, rows, model, today):
            write_json(data_dir / file_for("relations", rel["id"]), rel)
            written.append(rel)
    return written, calls


def closest(target, cands, vectors):
    """The embed.TOP_K candidates closest to target, or None when there are no vectors for target."""
    if not vectors or target["id"] not in vectors:
        return None
    keep = set(embed.nearest(vectors[target["id"]], vectors, [c["id"] for c in cands], embed.TOP_K))
    return [c for c in cands if c["id"] in keep]


def run(data_dir: Path, today: str, log=print, chat=None, embed_fn=None):
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent = agent_for(config, log)
    if not agent:
        log("compare: skipped (no llm.compare or llm.summary in config.json)")
        return []
    language = (config.get("summary") or {}).get("language", "Korean, keeping technical terms in English")
    max_calls = int((config.get("compare") or {}).get("max_calls_per_run", 150))
    papers = load_dir(data_dir / "papers")
    relations = load_dir(data_dir / "relations")
    state_path = data_dir / "state" / "compare.json"
    st = read_json(state_path, {"done_new": [], "done_liked": [], "checked": []})
    done_new, done_liked, checked = set(st["done_new"]), set(st["done_liked"]), set(st["checked"])
    known = existing_pairs(relations) | checked
    week_ago = (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()
    # key papers = liked + linked to a research topic (any relation that is not rejected, topic not retired)
    claims = load_dir(data_dir / "claims")
    open_claims = {cid for cid, c in claims.items() if c.get("status") != "retired"}
    linked = {r["from"] for r in relations.values() if r.get("layer") == "0-1" and r.get("to") in open_claims
              and r.get("status") != "rejected" and r.get("type") != "unrelated"}
    liked = [p for p in papers.values() if is_liked(p) or p["id"] in linked]
    visible = lambda p: not (p.get("marks") or {}).get("hidden")

    vectors = embed.ensure(data_dir, config, log, embed_fn=embed_fn, papers=papers)

    def pick(target, pool):
        pool = [q for q in pool if q["id"] != target["id"] and visible(q)]
        near = closest(target, pool, vectors)
        return near if near is not None else [q for q in pool if topic_set(target) & topic_set(q)]

    jobs = []  # (target, candidates, state set to mark)
    for p in papers.values():
        if p["id"] not in done_new and (p.get("entered_at") or "") >= week_ago and visible(p):
            jobs.append((p, pick(p, liked), done_new))
    for p in liked:
        if p["id"] not in done_liked:
            jobs.append((p, pick(p, list(papers.values())), done_liked))

    def save_state():
        st.update(done_new=sorted(done_new), done_liked=sorted(done_liked), checked=sorted(checked))
        write_json(state_path, st)

    written, calls = [], 0
    for target, cands, mark in jobs:
        cands = [c for c in cands if pair_key(target["id"], c["id"]) not in known]
        if calls + -(-len(cands) // BATCH) > max_calls:
            log(f"compare: reached {max_calls} calls, the rest waits for the next run")
            break
        try:
            new, used = compare_target(data_dir, agent, chat, language, target, cands, today, log)
        except llm.LLMUnavailable as e:
            log(f"compare: stopped ({e})")
            break
        calls += used
        written += new
        for c in cands:
            checked.add(pair_key(target["id"], c["id"]))
            known.add(pair_key(target["id"], c["id"]))
        mark.add(target["id"])
        save_state()
        if new:
            checkpoint.save(data_dir, f"Compare: {len(written)} relations proposed", log)
    save_state()
    checkpoint.save(data_dir, f"Compare: {len(written)} relations proposed", log)
    log(f"compare: {len(written)} relations proposed in {calls} calls")
    return written


def find_for(data_dir: Path, paper_id: str, today: str, log=print, chat=None, embed_fn=None):
    """관계 찾기: one paper vs the 20 closest papers by embedding (all papers without embeddings).
    Pairs that already have a relation are skipped."""
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent = agent_for(config, log)
    if not agent:
        raise llm.LLMUnavailable("no llm.compare or llm.summary in config.json")
    language = (config.get("summary") or {}).get("language", "Korean, keeping technical terms in English")
    papers = load_dir(data_dir / "papers")
    if paper_id not in papers:
        raise ValueError(f"paper {paper_id} not found")
    known = existing_pairs(load_dir(data_dir / "relations"))
    target = papers[paper_id]
    cands = [q for q in papers.values() if q["id"] != paper_id and pair_key(paper_id, q["id"]) not in known
             and not (q.get("marks") or {}).get("hidden")]
    near = closest(target, cands, embed.ensure(data_dir, config, log, embed_fn=embed_fn, papers=papers))
    if near is not None:
        cands = near
    else:  # the closest papers first, in case the run is cut short
        cands.sort(key=lambda q: -len(topic_set(target) & topic_set(q)))
    written, calls = compare_target(data_dir, agent, chat, language, target, cands, today, log)
    log(f"관계 찾기 {paper_id}: {len(written)} relations from {len(cands)} papers ({calls} calls)")
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--paper")
    a = ap.parse_args()
    if a.paper:
        find_for(Path(a.data), a.paper, a.today)
    else:
        run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
