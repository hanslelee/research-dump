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
- [x] Viewer: graph, tag-based layout, filters, claim view, detail panel
- [x] Buttons: confirm, hold, approve/reject relations, manual summary, manual relations, claims with versions and experiments (one commit per action through the GitHub API)
- [x] Import the public reading list into your data repository
- [x] Collection: Semantic Scholar keyword search (all venues) first, then arXiv for the newest preprints, plus papers citing your confirmed papers and OpenAlex by institution → `candidates/`
- [x] Metadata: venue, date, citations (refreshed weekly), affiliations and industry flag, popularity stars by citation speed
- [x] Abstracts filled in (arXiv, Semantic Scholar)
- [x] Screening agent (scope + tags, every decision logged) and summary agent (claim / condition / gap from the abstract)
- [x] Weekly run and Telegram notifications
- [ ] Compare agent
