"""Bring older data files up to the current shape. Safe to run every time (does nothing when up to date).

    python pipeline/migrate.py --data ../research-dump-data

Papers: status (new/hold/confirmed) -> marks {liked, bookmarked, hidden}, plus memo and entered_at.
Claims: memo field; the old 빈 곳 (gap) is appended to 근거 (basis) and the old 첫 실험 (first_experiment)
becomes a planned experiment.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import file_for, load_dir, normalize_paper, write_json  # noqa: E402


def normalize_claim(c: dict) -> bool:
    before = json.dumps(c, sort_keys=True)
    c.setdefault("memo", "")
    gap = (c.pop("gap", "") or "").strip()
    if gap:
        c["basis"] = f"{(c.get('basis') or '').strip()}\n빈 곳: {gap}".strip()
    fe = (c.pop("first_experiment", "") or "").strip()
    if fe:
        c["experiments"] = [{"title": "첫 실험", "status": "plan", "setup": fe, "result": ""}] + (c.get("experiments") or [])
    for x in c.get("experiments") or []:
        x.setdefault("status", "done" if (x.get("result") or "").strip() else "plan")
    return json.dumps(c, sort_keys=True) != before


def run(data_dir: Path, today: str, log=print) -> int:
    changed = 0
    for p in load_dir(data_dir / "papers").values():
        if normalize_paper(p, today):
            write_json(data_dir / file_for("papers", p["id"]), p)
            changed += 1
    for c in load_dir(data_dir / "claims").values():
        if normalize_claim(c):
            write_json(data_dir / file_for("claims", c["id"]), c)
            changed += 1
    log(f"migrate: {changed} files updated")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
