"""Summary agent: write the three-field summary (claim / condition / gap) for papers that have none.

    python pipeline/summarize.py --data ../research-dump-data

Works from the abstract only (not the full text) and says so in provenance.
A summary with an empty field fails the check and is kept with summary_check = "incomplete",
so it shows up for review instead of passing silently.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402
from common import file_for, load_dir, write_json  # noqa: E402

SYSTEM = """You summarize one research paper for a researcher looking for research gaps.
Use only the title and abstract given. Do not invent results that are not stated.
Write in {language}.

Fields:
- claim: what the paper shows or proposes, one or two sentences, with the main number if the abstract gives one.
- condition: the scope and conditions under which the claim holds: models, data, modality, setting, assumptions. Never leave this empty; if the abstract does not say, write what is missing.
- gap: what the paper does not cover or leaves open, as a reader would see it. One or two sentences.

Reply with JSON only: {{"claim": "...", "condition": "...", "gap": "..."}}"""


def run(data_dir: Path, today: str, log=print, chat=None):
    chat = chat or llm.chat
    config = json.loads((data_dir / "config.json").read_text())
    agent = (config.get("llm") or {}).get("summary")
    if not agent:
        log("summary: skipped (no llm.summary in config.json)")
        return []
    opts = config.get("summary") or {}
    language = opts.get("language", "Korean, keeping technical terms in English")
    max_items = int(opts.get("max_per_run", 60))

    papers = load_dir(data_dir / "papers")
    todo = [p for p in papers.values()
            if not (p.get("summary") or {}).get("claim") and (p.get("abstract") or "").strip()]
    skipped = sum(1 for p in papers.values() if not (p.get("summary") or {}).get("claim") and not (p.get("abstract") or "").strip())
    if skipped:
        log(f"summary: {skipped} papers have no abstract yet")
    done = []
    for p in todo[:max_items]:
        try:
            reply = chat(agent, SYSTEM.format(language=language), f"Title: {p['title']}\n\nAbstract: {p['abstract'][:4000]}")
            s = llm.parse_json(reply["text"])
        except llm.LLMError as e:
            log(f"summary: stopped ({e})")
            break
        summary = {k: str(s.get(k, "")).strip() for k in ("claim", "condition", "gap")}
        p["summary"] = summary
        p["provenance"] = {**(p.get("provenance") or {}), "summary_model": reply["model"], "summary_from": "abstract",
                           "summarized_at": today,
                           "summary_check": "ok" if all(summary.values()) else "incomplete"}
        write_json(data_dir / file_for("papers", p["id"]), p)
        done.append(p)
    log(f"summary: {len(done)} written")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
