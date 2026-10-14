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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkpoint  # noqa: E402
import llm  # noqa: E402
from common import clean_text, file_for, load_dir, write_json  # noqa: E402

HANGUL = re.compile(r"[\uac00-\ud7a3]")


def needs_summary(p, korean):
    claim = (p.get("summary") or {}).get("claim") or ""
    if not claim:
        return True
    # written by a model in the wrong language: redo (manual summaries are left alone)
    by_model = (p.get("provenance") or {}).get("summary_model") not in (None, "manual", "none")
    return korean and by_model and not HANGUL.search(claim)

SYSTEM = """You summarize one research paper for a researcher looking for research gaps.
Use only the title and abstract given. Do not invent results that are not stated.
Write every field in {language}. This is required even if the abstract is in English.

Fields, one sentence each:
- claim: what the paper does and what it finds, with the main number if the abstract gives one.
- condition: the scope under which the claim holds: models, data, modality, setting, assumptions. Never leave this empty; if the abstract does not say, write what is missing.
- gap: what the paper leaves open. Start with "(저자)" if the abstract itself states this limitation, otherwise "(추론)".

Plain text only: no LaTeX, no markdown.
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
    every = int(opts.get("save_every", 5))

    papers = load_dir(data_dir / "papers")
    korean = language.lower().startswith("korean")
    todo = [p for p in papers.values() if needs_summary(p, korean) and (p.get("abstract") or "").strip()]
    skipped = sum(1 for p in papers.values() if not (p.get("summary") or {}).get("claim") and not (p.get("abstract") or "").strip())
    if skipped:
        log(f"summary: {skipped} papers have no abstract yet")
    done = []
    for p in todo[:max_items]:
        try:
            reply = chat(agent, SYSTEM.format(language=language), f"Title: {clean_text(p['title'])}\n\nAbstract: {clean_text(p['abstract'])[:4000]}",
                         json_mode=True)
            s = llm.parse_json(reply["text"])
            if not isinstance(s, dict):
                raise llm.LLMError("reply is not an object")
        except llm.LLMUnavailable as e:
            log(f"summary: stopped ({e})")
            break
        except llm.LLMError as e:
            log(f"summary: skipped {p['id']} ({e})")
            continue
        summary = {k: clean_text(str(s.get(k, ""))) for k in ("claim", "condition", "gap")}
        if korean and summary["claim"] and not HANGUL.search(summary["claim"]):
            log(f"summary: skipped {p['id']} (not in Korean, will retry next run)")
            continue
        p["summary"] = summary
        p["provenance"] = {**(p.get("provenance") or {}), "summary_model": reply["model"], "summary_from": "abstract",
                           "summarized_at": today,
                           "summary_check": "ok" if all(summary.values()) else "incomplete"}
        write_json(data_dir / file_for("papers", p["id"]), p)
        done.append(p)
        log(f"summary: {p['id']} ({reply['model']})")
        if len(done) % every == 0:
            checkpoint.save(data_dir, f"Summaries: {len(done)} written", log)
    checkpoint.save(data_dir, f"Summaries: {len(done)} written", log)
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
