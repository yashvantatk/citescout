# 📚 CiteScout AI

**Literature Scout & Bibliography Builder** · Ziro Agentic AI Hackathon 2026 · Team Three Pointers

A faculty member types a research question, a year range and keywords. CiteScout's agent returns
**25 relevant open-access papers, every DOI verified**, merges preprints with their published versions,
lists paywalled papers separately (link only), flags bad or retracted citations, and, **only after the
user approves**, downloads the legal OA PDFs and builds a BibTeX file in one verified ZIP.

> **The LLM decides the strategy; code decides what counts.**

## Architecture

```
Faculty input ─▶ Gemini planner (function calling) ─▶ tools ─▶ deterministic gates ─┐
                       ▲                                                            │
                       └──────────── tally (cited / paywalled / flagged / pending) ◀┘
                                                       │
                     rank + annotate (quote-checked) ◀─┘
                                 │
                 HUMAN APPROVAL (exact plan hash) ─▶ download legal OA PDFs + BibTeX + ZIP
                                                              │
                                              post-action verifier (9 checks)
```

| Agent tool | What it does |
|---|---|
| `search_arxiv` | keyword search on arXiv (all hits are green OA) |
| `search_s2` | Semantic Scholar search, OpenAlex fallback; finds published versions |
| `verify_pool` | gate: OA check → DOI verification → retraction check |
| `snowball` | follows references / citations of a strong cited paper (provenance kept) |
| `inspect_publisher` | landing-page evidence for paywalled items (never bypasses paywalls) |
| `finish` | stop; **rejected by code** until Semantic Scholar was searched and pending papers are verified |

**Deterministic gates (code, not the LLM)**
- **DOI verification:** resolve at doi.org (CSL-JSON), title fuzzy match ≥ 90 + author overlap + year ±1
- **Version merging:** shared IDs (DOI / arXiv / S2) first, then fuzzy title with a Part I/II guard
- **Open access:** Unpaywall → OpenAlex → fail closed (unknown = not downloadable)
- **Integrity:** Crossref `updates:` filter (incl. Retraction Watch) → retracted papers flagged
- **Annotation:** Gemini scores topic/method + writes "why cite"; its evidence quote must appear verbatim in the abstract
- **Approval:** `execute()` refuses unless the approval matches the exact plan hash; paywalled items can never enter the plan
- **Post-action verifier:** re-opens the ZIP: files present, BibTeX re-parses, unique keys, every entry has a DOI, every PDF has `%PDF` magic, PDFs ↔ keys, plan hash, no paywalled download, SHA-256 match
- **Idempotency:** the same approved plan is never downloaded twice

## Tech stack
Python 3.12 · **Gemini 3.8 Flash** via `google-genai` function calling (fallback 3.6 Flash → 3.5 Flash-Lite,
scripted planner if the API is down) · Streamlit · httpx + tenacity + SQLite response cache · RapidFuzz ·
bibtexparser · SQLite audit DB · APIs: arXiv, Semantic Scholar, OpenAlex, doi.org, Unpaywall, Crossref.

## Files
| File | Role |
|---|---|
| `agent.py` | Gemini function-calling loop, 6 tools, finish gate, scripted fallback |
| `scout_engine.py` | search, enrichment, OA check, merging, snowballing, classification |
| `verifier.py` | doi.org resolution + title/author/year match |
| `integrity.py` | retraction / correction check |
| `annotate.py` | relevance breakdown, why-cite, verbatim-quote gate |
| `export.py` | plan → approval → PDFs + BibTeX + ZIP → post-action verifier |
| `replay.py` | record live runs, replay offline |
| `net.py` | pacing, retries, cache, per-host metrics, source-evidence log |
| `db.py` | runs, source_evidence, paper_decisions, test_case_runs |
| `app.py` | Streamlit UI: Scout · Metrics · Test cases · How it works |
| `test_scenarios.py` | TC1–TC6 live; `tests_agent_mock.py`, `tests_export.py` offline |

## Test cases
| TC | Scenario | Expected | Result |
|---|---|---|---|
| TC1 | topic query | 25 papers, all DOIs resolved | ✅ |
| TC2 | ResNet arXiv 1512.03385 + CVPR DOI | merged into one entry | ✅ |
| TC3 | paywalled 10.1126/science.aaa8415 | link only, no download | ✅ |
| TC4 | DOI resolves to a different title | flagged, not cited | ✅ |
| TC5 (extra) | retracted paper (Wakefield 1998) | flagged, not cited | ✅ |
| TC6 (extra) | download without approval | blocked; paywalled never planned | ✅ |

```bash
python test_scenarios.py                         # TC1–TC6 live, stored in citescout.db
pytest -q tests_agent_mock.py tests_export.py    # offline: agent loop, finish gate, approval, export
```

## Run locally
```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                              # add GEMINI_API_KEY, S2_API_KEY, OPENALEX_API_KEY, CONTACT_EMAIL
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)
1. Push this repo to GitHub (`.env` is gitignored; commit `citescout.db` and `replays/` for the demo).
2. share.streamlit.io → New app → pick the repo, main file `app.py`, **Advanced settings → Python 3.12**.
3. Settings → Secrets: paste `.streamlit/secrets.toml.example` with real values.
