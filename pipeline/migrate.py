"""Bring older data files up to the current shape. Safe to run every time (does nothing when up to date).

    python pipeline/migrate.py --data ../research-dump-data

Papers: status (new/hold/confirmed) -> marks {liked, bookmarked, hidden}, plus memo and entered_at.
Claims: memo field.
"""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import file_for, load_dir, normalize_paper, write_json  # noqa: E402


def run(data_dir: Path, today: str, log=print) -> int:
    changed = 0
    for p in load_dir(data_dir / "papers").values():
        if normalize_paper(p, today):
            write_json(data_dir / file_for("papers", p["id"]), p)
            changed += 1
    for c in load_dir(data_dir / "claims").values():
        if "memo" not in c:
            c["memo"] = ""
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
