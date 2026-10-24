# Setup

research-dump runs on two repositories:

| Repository | Visibility | Holds |
| --- | --- | --- |
| `research-dump` (this one) | public | viewer, pipeline code, schemas, demo data |
| `<you>/research-dump-data` | **private** | your papers, claims, relations, experiments, secrets |

Your data never enters this repository.

## 1. Create your data repository

1. Create a **private** repository, e.g. `research-dump-data`.
2. Copy everything in [`templates/data-repo/`](../templates/data-repo) into it
   (`config.json`, the four empty folders, `.github/workflows/weekly.yml` and `.github/workflows/requests.yml`).
   `requests.yml` runs the **관계 찾기** and **연구 주제 추천** buttons; without it those requests wait forever.
3. Edit `config.json`: set `scope` to your research scope in one line and pick a model for each agent.

## 2. Add secrets

In your data repository: **Settings → Secrets and variables → Actions → New repository secret**.

| Name | Where to get it |
| --- | --- |
| `S2_API_KEY` | semanticscholar.org/product/api (request form) |
| `OPENALEX_API_KEY` | free account at openalex.org |
| `GEMINI_API_KEY` | aistudio.google.com |
| `OPENROUTER_API_KEY` | openrouter.ai (optional) |
| `TELEGRAM_BOT_TOKEN` | `@BotFather` → `/newbot` |
| `TELEGRAM_CHAT_ID` | send your bot a message, then open `https://api.telegram.org/bot<token>/getUpdates` |

Only the names in `config.json` → `llm.*.key_name` are needed for the LLM part. Any OpenAI-compatible endpoint works.

## 3. Open the viewer

1. Create a **fine-grained** GitHub token: Settings → Developer settings → Personal access tokens → Fine-grained.
   - Repository access: only your data repository
   - Permissions: **Contents: Read and write**
2. Open the viewer (`https://<owner>.github.io/research-dump/viewer/`), press **내 저장소 연결**, enter `owner/research-dump-data`, the branch and the token.

The token is stored only in your browser and sent only to `api.github.com`. Press **로그아웃** on shared computers.

The **재요약** button in a paper's panel calls the summary model (`config.json` → `llm.summary`) straight from your browser. The first time, it asks for that model's API key; the key is kept in this browser only, sent only to that model's address, and removed by **로그아웃**.

## 4. Collection settings

`config.json` → `collect`:

| Key | Meaning |
| --- | --- |
| `arxiv_categories`, `arxiv_keywords` | arXiv search: any category AND any keyword in the abstract |
| `s2_citations_of` | `"liked"`: new papers citing the papers you liked or that are ★ (default; `"confirmed"` means the same). `"all"`: every paper |
| `openalex_institutions` | OpenAlex institution ids, e.g. `["I1234567890"]` |
| `openalex_keywords` | keywords for the institution search |
| `days` | how far back the first run looks |

Find institution ids:

```
OPENALEX_API_KEY=... python pipeline/collect.py --find-institution "Anthropic"
```

Each run writes `candidates/<date>.json` and remembers what it has seen in `state/collect.json`, so a paper is proposed once.

## 5. What runs when

Weekly (`weekly.yml`): migrate old files → validate → collect → abstracts → screening → merge duplicates →
venue/citations/affiliations → summary → compare (relations) → Telegram → `bundle.json` → commit.

On request (`requests.yml`, starts when the web page saves a file under `requests/`):

| Button | What happens | Model |
| --- | --- | --- |
| 관계 찾기 | that paper vs every other paper; proposals appear as `new` relations | `llm.compare` (falls back to `llm.summary`) |
| 관련 논문 찾기 (연구 주제 화면) | that research topic vs every paper; support / narrow / conflict / duplicate / motivates drafts | `llm.compare` (falls back to `llm.summary`) |
| 추가하기 (arXiv id, link or title) | fetch the paper → tag → summarize → relation drafts. From a research topic: with that topic and with other papers. From 논문 목록: with every research topic that is not retired | screening, summary and compare models; `S2_API_KEY` for title search |
| 연구 주제 추천 | that paper + up to 4 papers linked by approved extends/conflicts (or, if none, liked papers with a shared topic). arXiv PDFs are downloaded and their text sent; 3 candidates appear in the 연구 주제 tab | `llm.recommend` (falls back to `llm.summary`) |

Memos are never sent to a model.

`bundle.json` packs every paper, author, claim and relation (with each file's git sha) so the web page loads in one request.
Files changed after it was built are fetched one by one, so it is never stale.

## 6. Check it

- Actions tab of your data repository → **weekly** → **Run workflow**. It validates your data.
- Locally: `python pipeline/validate.py <path-to-your-data-repo>`.
