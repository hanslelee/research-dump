"""Shared helpers for pipeline scripts."""
import json
import re
from pathlib import Path

METHOD = ["train", "inference", "eval", "analysis", "other"]
GOAL = ["harmlessness", "honesty", "helpfulness"]
MODAL = ["text", "multimodal"]
SETTING = ["standalone", "rag", "agent"]


def file_for(dir_name: str, pid: str) -> str:
    return f"{dir_name}/{pid.replace(':', '_').replace('/', '_')}.json"


def load_dir(folder: Path) -> dict:
    if not folder.exists():
        return {}
    return {d["id"]: d for d in (json.loads(p.read_text()) for p in sorted(folder.glob("*.json")))}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


# ---------- paper marks (좋아요 / 북마크 / 숨김) ----------
MARKS = ("liked", "bookmarked", "hidden")


def new_marks() -> dict:
    return {"liked": False, "bookmarked": False, "hidden": False}


def normalize_paper(p: dict, today: str) -> bool:
    """Bring an older paper file up to the current shape. Returns True if anything changed.

    status "confirmed" -> 좋아요, "hold" -> 북마크, "new" -> nothing marked.
    """
    before = json.dumps(p, sort_keys=True)
    marks = {**new_marks(), **(p.get("marks") or {})}
    status = p.pop("status", None)
    pv = p.get("provenance") or {}
    if status == "confirmed" and not marks["liked"]:
        marks["liked"] = True
        marks["liked_at"] = pv.get("confirmed_at") or pv.get("created_at") or today
    if status == "hold":
        marks["bookmarked"] = True
    p["marks"] = marks
    p.setdefault("memo", "")
    if not p.get("entered_at"):
        p["entered_at"] = pv.get("created_at") or today
    return json.dumps(p, sort_keys=True) != before


def is_liked(p: dict) -> bool:
    return bool((p.get("marks") or {}).get("liked")) or p.get("status") == "confirmed"


def is_starred(p: dict) -> bool:
    return str((p.get("meta") or {}).get("popularity") or "").startswith("★")


def topic_set(p: dict) -> set:
    return set((p.get("tags") or {}).get("topic") or [])


def matched_keywords(text: str, keywords: list) -> list:
    t = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    return [k for k in keywords if " " + re.sub(r"[^a-z0-9]+", " ", k.lower()).strip() + " " in t]


def clean_tags(raw: dict, topics: list) -> dict:
    """Coerce model output into allowed tag values."""
    raw = raw or {}
    as_list = lambda v: v if isinstance(v, list) else ([v] if v else [])
    method = [m for m in as_list(raw.get("method")) if m in METHOD] or ["other"]
    main = raw.get("main_method") if raw.get("main_method") in method else method[0]
    goal = [g for g in as_list(raw.get("goal")) if g in GOAL]
    modal = raw.get("modal") if raw.get("modal") in MODAL else "text"
    setting = raw.get("setting") if raw.get("setting") in SETTING else "standalone"
    topic = [t for t in as_list(raw.get("topic")) if t in topics]
    return {"method": method, "main_method": main, "goal": goal, "modal": modal, "setting": setting, "topic": topic}


def clean_text(text: str) -> str:
    """Strip LaTeX commands and control characters that break JSON and Telegram.

    \\textbf{X} -> X, \\bench{} -> bench, $x$ -> x, stray backspace/form-feed characters removed.
    """
    t = text or ""
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)          # control characters (e.g. \b from "\\bench")
    for _ in range(3):
        t = re.sub(r"\\[A-Za-z]+\*?\{([^{}]+)\}", r"\1", t)        # \cmd{inner} -> inner
    t = re.sub(r"\\([A-Za-z]+)\*?(\{\})?", r"\1", t)                # \bench{} or \ours -> its name
    t = t.replace("$", "").replace("{}", "")
    return re.sub(r"\s+", " ", t).strip()
