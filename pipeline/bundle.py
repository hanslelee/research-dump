"""Bundle every paper, author, claim and relation into one file (bundle.json) so the web page loads fast.

    python pipeline/bundle.py --data ../research-dump-data

Each entry keeps the file's git blob sha next to its content. The web page compares those with the
repository tree and fetches only the files that changed after the bundle was built
(for example, edits made on the web page), so the bundle can never hide newer data.

Also included: candidates the screening agent excluded in the last 4 weeks (for the 제외됨 filter)
and keyword statistics (state/keywords.json).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_json  # noqa: E402

DIRS = ("papers", "authors", "claims", "relations")
EXCLUDED_DAYS = 28


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def excluded_candidates(data_dir: Path, today: str, paper_ids: set) -> list:
    since = (dt.date.fromisoformat(today) - dt.timedelta(days=EXCLUDED_DAYS)).isoformat()
    cands = {}
    for f in sorted((data_dir / "candidates").glob("*.json")) if (data_dir / "candidates").exists() else []:
        if f.stem >= since:
            for it in read_json(f, {"items": []}).get("items", []):
                cands[it["id"]] = it
    out = {}
    for f in sorted((data_dir / "logs" / "screening").glob("*.json")) if (data_dir / "logs" / "screening").exists() else []:
        if f.stem < since:
            continue
        for d in read_json(f, []):
            if d.get("in_scope"):
                out.pop(d["id"], None)
                continue
            if d["id"] in paper_ids:  # a person included it later
                continue
            c = cands.get(d["id"], {})
            out[d["id"]] = {**c, "id": d["id"], "title": d.get("title") or c.get("title", ""),
                            "screen_reason": d.get("reason", ""), "screen_model": d.get("model", ""), "screened_at": f.stem}
    return sorted(out.values(), key=lambda x: x["screened_at"], reverse=True)


def run(data_dir: Path, today: str, log=print) -> Path:
    bundle = {"generated_at": today, "entries": {}}
    for d in DIRS:
        for f in sorted((data_dir / d).glob("*.json")) if (data_dir / d).exists() else []:
            raw = f.read_bytes()
            bundle["entries"][f"{d}/{f.name}"] = {"sha": blob_sha(raw), "data": json.loads(raw)}
    paper_ids = {e["data"]["id"] for k, e in bundle["entries"].items() if k.startswith("papers/")}
    bundle["excluded"] = excluded_candidates(data_dir, today, paper_ids)
    bundle["keywords"] = read_json(data_dir / "state" / "keywords.json", {})
    out = data_dir / "bundle.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n")
    log(f"bundle: {len(paper_ids)} papers, {len(bundle['excluded'])} excluded candidates -> bundle.json "
        f"({out.stat().st_size // 1024} KB)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
