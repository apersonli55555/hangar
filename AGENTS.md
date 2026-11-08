# Instructions for AI assistants working on Hangar

Read this before changing anything. It applies to any assistant (ChatGPT,
Codex, Claude, Copilot) and to people.

## What Hangar is

A free aerospace & defense investing research site, live at
https://apersonli55555.github.io/hangar/. Built by a high-school student who
wants to understand every part well enough to explain it in an interview, so
**explain your changes plainly** and keep code readable over clever.

- Phase 1 (done): daily Department of War contract announcements → parsed
  awards (`data/awards.csv`) → ticker mapping → static site.
- Phase 2 (done): backlog and revenue from SEC EDGAR, and each award's size
  relative to them ("materiality"). See `docs/phase2_task.md`.
- Phase 3 (done): timestamped stock pitches stored as files (`pitches/`,
  `pipeline/pitches.py`). Published pitch text is locked: never edit it.

Architecture, data files and parsing rules are documented in `README.md`.

## Non-negotiable rules

1. **Free data and free hosting only.** No paid APIs, no API keys for price data.
2. **No stock prices on the site.** The free price APIs (Tiingo, Massive/Polygon,
   Alpha Vantage) forbid public display. Linking out to Yahoo Finance is fine;
   fetching or showing prices is not.
3. **Every number traces to its source.** Each figure on the site links to the
   government document it came from. Tests enforce this (`tests/test_site.py`).
4. **Never invent or estimate data silently.** If a rule can't extract a value
   cleanly, leave it empty and record why (`review_reasons` / `notes`).
   Never "fix" a number in the government's text; flag it.
5. **Respect site rules.** Honest User-Agent with a contact from the
   `HANGAR_CONTACT` environment variable (see `pipeline/http.py`); slow,
   polite request rates; never bypass bot protection (war.gov blocks bots, so we
   read Internet Archive copies). SEC EDGAR: max 10 requests/second (go slower).
6. **No personal email or secrets in code, commits or logs.**
7. **"Not investment advice"** stays on every page.
8. **Stop and check with the user at the end of each phase.**

## How to work

- **Run the tests before every push:** `python -m pytest -q` (310+ tests).
  Every new rule gets a test built from a real example. Never weaken or delete
  a test to make it pass; fix the code or explain why the test was wrong.
- **Python 3.12, standard library + `jinja2` + `pytest` only.** Don't add
  dependencies without asking.
- **No JavaScript on the site.** Charts are SVG drawn in Python
  (`pipeline/site.py`).
- **Build and preview the site:**
  `python -m pipeline.site` then `python -m http.server 8765 --directory site`.
  Check desktop and phone width (375px, no sideways scroll), light and dark.
- **Branches and PRs:** work on a branch and open a pull request to `main`;
  don't push straight to `main`. A bot commits data to `main` twice a day, so
  rebase on `main` before opening the PR. The user merges.
- **Small, reviewable commits** with clear messages.

## Where things are

| Path | What |
|---|---|
| `pipeline/sources.py`, `pipeline/http.py` | fetching (war.gov RSS, Internet Archive) |
| `pipeline/parse.py` | announcement text → award records |
| `pipeline/tickers.py`, `data/ticker_aliases.csv` | company name → ticker rules |
| `pipeline/run.py` | backfill / daily / build commands |
| `pipeline/metrics.py` | what counts as definite / potential / excluded |
| `pipeline/site.py`, `site_src/` | static site generator and templates |
| `.github/workflows/daily.yml` | twice-daily job: test, fetch, build, deploy |
| `tests/` | all tests; `tests/fixtures/` holds real saved pages |
