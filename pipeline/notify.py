"""Send a Telegram message listing papers summarized in this run.

    python pipeline/notify.py --data ../research-dump-data

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Skips quietly without them.
"""
import argparse
import datetime as dt
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_dir, read_json  # noqa: E402


def week_ago(today):
    return (dt.date.fromisoformat(today) - dt.timedelta(days=7)).isoformat()


def meta_line(p):
    """NeurIPS 2025 · Google DeepMind, NUS · 2026.10 · 인용 34"""
    m = p.get("meta") or {}
    parts = []
    venue = m.get("venue") or p.get("venue") or ""
    year = m.get("year") or p.get("year")
    if venue:
        parts.append(venue if venue == "arXiv" or not year else f"{venue} {year}")
    if m.get("institutions"):
        parts.append(", ".join(m["institutions"][:2]) + (" 외" if len(m["institutions"]) > 2 else ""))
    date = (m.get("published") or p.get("published") or "")[:7]
    if len(date) == 7:
        parts.append(date.replace("-", "."))
    if m.get("citations") is not None:
        parts.append(f"인용 {m['citations']}")
    return " · ".join(parts)


def tag_line(p):
    t = p.get("tags") or {}
    tags = [*(t.get("method") or []), *(t.get("goal") or []), t.get("modal"), t.get("setting"), *(t.get("topic") or [])]
    return " ".join(f"#{x}" for x in tags if x)


def paper_block(p):
    m = p.get("meta") or {}
    s = p.get("summary") or {}
    star = m.get("popularity") if (m.get("popularity") or "").startswith("★") else ""
    title = f'<a href="{html.escape(p.get("url") or "")}">{html.escape(p["title"])}</a>'
    lines = [f"{star} {title}".strip()]
    for line in (meta_line(p), tag_line(p)):
        if line:
            lines.append(html.escape(line))
    if s.get("claim"):
        lines.append(html.escape(s["claim"]))
    if (p.get("provenance") or {}).get("summary_check") == "incomplete":
        lines.append("⚠ 요약 일부가 비어 있어요")
    return "\n".join(lines)


def keyword_line(stats, top=6):
    """jailbreak 12 · sycophancy 5 · ... (papers included this run, per keyword)"""
    rows = sorted(((k, v.get("included", 0)) for k, v in (stats or {}).items() if v.get("included")), key=lambda x: -x[1])
    return " · ".join(f"{k} {n}" for k, n in rows[:top])


def build_messages(papers, viewer_url, limit=3900, keywords=None, new_relations=0):
    if not papers and not new_relations:
        return []
    head = f"<b>research-dump · 새 논문 {len(papers)}편</b>"
    if new_relations:
        head += f"\n새 관계 후보 {new_relations}개 (웹페이지에서 승인/거절)"
    kl = keyword_line(keywords)
    if kl:
        head += "\n" + html.escape(kl)
    if viewer_url:
        head += f'\n<a href="{html.escape(viewer_url)}">웹페이지에서 확인</a>'
    msgs, cur = [], head
    for p in papers:
        block = "\n\n" + paper_block(p)
        if len(cur) + len(block) > limit:
            msgs.append(cur)
            cur = block.lstrip("\n")
        else:
            cur += block
    msgs.append(cur)
    return msgs


def run(data_dir: Path, today: str, log=print):
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("notify: skipped (Telegram secrets not set)")
        return 0
    config = json.loads((data_dir / "config.json").read_text())
    papers = [p for p in load_dir(data_dir / "papers").values()
              if (p.get("provenance") or {}).get("summarized_at") == today and p.get("entered_at", today) >= week_ago(today)
              and not (p.get("marks") or {}).get("hidden")]
    rank = {"★★": 0, "★": 1}
    papers.sort(key=lambda p: (rank.get((p.get("meta") or {}).get("popularity"), 2), p["title"]))
    kw = read_json(data_dir / "state" / "keywords.json", {})
    kw = kw.get("last_run") if kw.get("last_run_date") == today else {}
    new_rel = sum(1 for r in load_dir(data_dir / "relations").values() if r.get("status") == "new" and r.get("created_at") == today)
    msgs = build_messages(papers, (config.get("notify") or {}).get("viewer_url", ""), keywords=kw, new_relations=new_rel)
    for m in msgs:
        body = json.dumps({"chat_id": chat_id, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            log(f"notify: failed ({e})")
            return 0
    log(f"notify: {len(papers)} papers in {len(msgs)} message(s)")
    return len(papers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
