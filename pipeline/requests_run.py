"""Process requests made on the web page (requests/*.json), then rebuild bundle.json.

    python pipeline/requests_run.py --data ../research-dump-data

Kinds:
  relations     관계 찾기: the paper vs every other paper (compare.find_for)
  topics        연구 주제 추천: research topic candidates from the paper and its linked papers (topics.recommend)
  claim_papers  관련 논문 찾기: a research topic vs every paper (claimrel.link)
  add_paper     추가하기: add a paper by arXiv id or title, tag and summarize it, then draft relations:
                from a research topic screen (claim set): with that topic + 관계 찾기 against other papers
                from the 논문 목록 tab (no claim): with every research topic that is not retired

A finished request file is deleted (the web page sees it disappear and reloads).
A failed one stays with status "failed" and the error, so the web page can show it and offer a retry.
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
import addpaper  # noqa: E402
import bundle  # noqa: E402
import claimrel  # noqa: E402
import compare  # noqa: E402
import topics  # noqa: E402
from common import load_dir, read_json, write_json  # noqa: E402


def telegram(text, log=print):
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        log(f"requests: telegram failed ({e})")


def run(data_dir: Path, today: str, log=print, chat=None, fetch=None, find=None):
    folder = data_dir / "requests"
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    papers = load_dir(data_dir / "papers")
    viewer = (read_json(data_dir / "config.json", {}).get("notify") or {}).get("viewer_url", "")
    done = 0
    for f in files:
        req = json.loads(f.read_text())
        if req.get("status") == "dropped":  # removed on the web page
            f.unlink()
            continue
        if req.get("status") == "failed":
            continue
        pid, kind = req.get("paper"), req.get("kind")
        title = html.escape((papers.get(pid) or {}).get("title", pid or ""))
        claims = load_dir(data_dir / "claims")
        try:
            if kind == "add_paper":
                pid, added = addpaper.add(data_dir, req.get("query", ""), today, log, chat=chat, **({"find": find} if find else {}))
                title = html.escape(load_dir(data_dir / "papers")[pid]["title"])
                if req.get("claim"):
                    rels = claimrel.link(data_dir, req["claim"], today, log, chat=chat, paper_ids={pid})
                    rels += compare.find_for(data_dir, pid, today, log, chat=chat)
                else:
                    rels = []
                    for c in claims.values():
                        if c.get("status") != "retired":
                            rels += claimrel.link(data_dir, c["id"], today, log, chat=chat, paper_ids={pid})
                msg = (f"<b>논문 추가</b> · {title}\n" + ("요약까지 끝났어요." if added else "이미 있던 논문이에요.")
                       + f" 관계 초안 {len(rels)}개 (웹페이지에서 승인/거절)")
            elif kind == "claim_papers":
                cid = req.get("claim")
                rels = claimrel.link(data_dir, cid, today, log, chat=chat)
                msg = f"<b>관련 논문 찾기</b> · {html.escape((claims.get(cid) or {}).get('text', cid or ''))}\n관계 초안 {len(rels)}개 (웹페이지에서 승인/거절)"
            elif kind == "relations":
                rels = compare.find_for(data_dir, pid, today, log, chat=chat)
                msg = f"<b>관계 찾기</b> · {title}\n새 관계 후보 {len(rels)}개 (웹페이지에서 승인/거절)"
            elif kind == "topics":
                kw = {"fetch": fetch} if fetch else {}
                claims = topics.recommend(data_dir, pid, today, log, chat=chat, **kw)
                msg = f"<b>연구 주제 추천</b> · {title}\n" + "\n".join(f"• {html.escape(c['text'])}" for c in claims)
            else:
                raise ValueError(f"unknown request kind {kind!r}")
        except Exception as e:  # keep the request so the page can show the error
            log(f"requests: {f.name} failed ({e})")
            write_json(f, {**req, "status": "failed", "error": str(e)[:300], "failed_at": today})
            continue
        f.unlink()
        done += 1
        if viewer:
            msg += f'\n<a href="{html.escape(viewer)}">웹페이지에서 보기</a>'
        telegram(msg, log)
    bundle.run(data_dir, today, log)
    log(f"requests: {done} done")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    run(Path(a.data), a.today)


if __name__ == "__main__":
    main()
