"""Shared helpers for pipeline scripts."""
import json
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
