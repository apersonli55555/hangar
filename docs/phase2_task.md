# Phase 2 task: backlog, revenue and materiality from SEC EDGAR

Follow `AGENTS.md`. Work in this order and **stop at each checkpoint for the
user's OK**.

## Checkpoint 1: plan first, no code

Write a short plan (one page) and wait for approval:
- which XBRL tags you'll use per company, and why;
- how you handle fiscal years, duplicates and missing data;
- what the company page will show;
- the files you'll add or change.

## The work (after approval)

1. **Fetch** (`pipeline/edgar.py`): for each ticker in `CORE` in
   `pipeline/site.py`, download the free XBRL "company facts" JSON:
   `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
   (10-digit, zero-padded CIK; look CIKs up in
   `https://www.sec.gov/files/company_tickers.json`).
   Use `pipeline/http.py` for every request so the User-Agent carries the
   `HANGAR_CONTACT` contact. Stay far below 10 requests/second.
   Ask the user for the project email before making SEC requests locally.

2. **Extract** two series per company, annual and quarterly where available:
   - **Backlog:** prefer `us-gaap:RevenueRemainingPerformanceObligation`.
     "Backlog" isn't a standard tag; some companies only state it in 10-K text.
     If the tag is missing, record that. **Don't estimate it.**
   - **Revenue:** `us-gaap:Revenues` or
     `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`. Pick per
     company, write the choice down, and keep it consistent over time.
   - Handle fiscal years that don't end in December (TDG ends in September).
   - The same fact appears in several filings: dedupe by period end (and
     duration), keeping the most recently filed value, and record which filing.
   - Keep 10-K/10-Q values separate from amended filings only if they differ.

3. **Store** in `data/` as CSV. Every value needs: ticker, tag, period start and
   end, value, form (10-K/10-Q), filed date, **accession number** and a
   **link to the filing**. Every number must trace to a document.

4. **Show** on each company page (SVG in Python, no JavaScript):
   - backlog, revenue, and backlog ÷ trailing-12-month revenue over time,
     each point linked to its filing;
   - **materiality** for each definite award: award as % of the latest backlog
     and trailing revenue **known on the award date** (as-of, no look-ahead:
     use a filing only if it was filed before the award);
   - an explicit "not reported in XBRL" message for gaps, never a blank chart
     that looks like zero.
   Add a short methodology section explaining all of the above.

5. **Tests** for every rule: dedupe, fiscal years, as-of matching, missing
   tags. Use real companyfacts JSON saved in `tests/fixtures/edgar/` (trim to
   the tags you use to keep them small). Extend `tests/test_site.py` so every
   backlog/revenue number on the site links to an SEC filing. All existing
   tests must keep passing.

6. **Automate:** add an EDGAR refresh to `.github/workflows/daily.yml` (once a
   day is plenty) and update `README.md`.

## Checkpoint 2: done

Open a pull request from a branch (rebased on `main`) with:
- what changed and how to explain it simply;
- which companies have backlog gaps, and why;
- test results;
- screenshots or notes from a desktop and phone preview.

Then stop. The user reviews before Phase 3.
