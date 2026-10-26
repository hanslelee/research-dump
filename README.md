# research-dump

A personal research knowledge base for finding research gaps.

New papers (layer 0) are collected, screened against your research scope, summarized, and linked to your research claims (layer 1). Agents draft; you confirm.

```
collect → screening agent → summary agent → you confirm → compare agent → you approve relations
```

- **Layer 0 · papers**: tagged on five axes (method, goal, modal, setting, topic). Papers with similar tags sit close together. Relations: `extends`, `conflicts`, and same-author via author nodes.
- **Layer 1 · claims**: your research claims, managed by you. Papers relate to a claim as `support`, `narrow`, `conflict`, `duplicate` or `motivates`. Pick a claim to see only its papers.

## Layout

| Path | What |
| --- | --- |
| `viewer/` | web viewer (static, GitHub Pages) |
| `pipeline/` | agents and scripts (`llm.py`: one call shape for any OpenAI-compatible LLM) |
| `schemas/` | JSON formats for paper, claim, relation, author, config |
| `sample-data/` | demo data: public papers only, demo claims |
| `templates/data-repo/` | starter files for your private data repository |

## Use it

Each researcher runs their own copy. Code is public; **your data lives in your own private repository**. See [docs/setup.md](docs/setup.md).

Try the demo: open `viewer/` with the sample data (no token needed).

## Status

- [x] Schemas and sample data
- [x] Web page tabs: 홈 (graph; default shows liked + last 4 weeks + ★), 논문 목록 (sort by entry date / citations / popularity, filters, excluded candidates), 연구 주제 (claims with memo, versions, experiments, AI candidates)
- [x] Buttons: 좋아요 / 북마크 / 숨김, memo, 재요약, 관계 찾기, 연구 주제 추천, approve/reject relations, manual summary and relations (one commit per action through the GitHub API)
- [x] KO / EN switch (interface text; your data translated on the fly with your key; demo data carries stored English)
- [x] 데모로 내보내기: chosen research topics with their papers and relations become the public demo (`sample-data/showcase.json`), memos never included
- [x] `bundle.json`: one file for fast loading, changed files fetched on top
- [x] Import the public reading list into your data repository
- [x] Collection: Semantic Scholar keyword search (all venues) first, then arXiv for the newest preprints, plus papers citing your liked or ★ papers and OpenAlex by institution → `candidates/`
- [x] Metadata: venue, date, citations (refreshed weekly), affiliations and industry flag, popularity stars by citation speed
- [x] Abstracts filled in (arXiv, Semantic Scholar)
- [x] Screening agent (scope + tags, every decision logged) and summary agent (claim / condition / gap from the abstract)
- [x] Weekly run and Telegram notifications
- [x] Duplicate papers merged automatically; keyword statistics (Telegram and 논문 목록)
- [x] Compare agent: new papers vs key papers (liked or linked to a research topic), strict, evidence sentence kept, approve/reject on the web page
- [x] 연구 주제 추천 from a paper and its linked papers (PDF text), saved as AI candidates
- [x] 베이스라인 · 주요 참고 논문 per paper (Semantic Scholar references, method/influential first, add as approved extends)
- [x] 저장소에서 관련 논문 찾기 and 관련 논문 웹 검색 (Semantic Scholar, sorted by venue / citations / top company) for a research topic, and 추가하기 for papers not in the repository (saved, summarized and linked in one request)
