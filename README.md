# Hangar

An aerospace & defense research hub. Phase 1: daily Department of War
contract awards, parsed, mapped to tickers, and linked to their sources.
Phase 2: each company's backlog (RPO) and revenue from SEC filings, and every
award's size relative to them ("materiality").

**Not investment advice.**

## Status

- [x] 1a: parser + tests on 12 real days of announcements
- [x] 1b: backfill from the archive (Jan 2024 onward), ticker mapping, daily GitHub Action
- [x] 1c: static site (home, 36 company pages + year pages, methodology)
- [x] 1d: price link-outs (no price data shown: free APIs forbid public display; see
      `docs/tiingo_permission_request.md`), analytics slot, deploy workflow
- [x] 2: backlog (RPO) and revenue from SEC EDGAR, backlog/revenue charts, award materiality
- [x] 3: timestamped pitches (fingerprinted, locked, archived), "what happened since" from Hangar data
- [ ] Launch: see `LAUNCH.md` (needs your GitHub account, project email, Git)

## How data flows

```
war.gov RSS feed ──► list of daily "Contracts for <date>" pages   (pipeline/sources.py)
        │
Internet Archive ──► saved copy of each page (war.gov blocks bots) (pipeline/sources.py)
        │
   parse.py      ──► one record per award, with the exact source text
        │
 data/*.csv      ──► (next phases) tickers, prices, static site
```

## Phase 2: backlog, revenue and materiality (pipeline/edgar.py)

```
sec.gov company_tickers.json ──► CIK for each core ticker      (data/edgar_companies.csv)
data.sec.gov companyfacts    ──► every XBRL number in 10-K/10-Q (cached in data/edgar/, not committed)
        │  edgar.py: pick tags, dedupe, label periods, find gaps
data/financials.csv          ──► one row per value + accession, form, filed date, filing link
data/edgar_coverage.csv      ──► per company & metric: reported / gaps / not reported in XBRL
        │  metrics.materiality(): award ÷ latest RPO and revenue filed by the award date
company pages                ──► charts + "% of RPO / revenue" column, every number linked
```

| Rule | Why |
|---|---|
| Backlog = `RevenueRemainingPerformanceObligation` (RPO) | The only standard tag. It excludes unfunded orders/options, so it is smaller than press-release "backlog". Not read from 10-K text. |
| Revenue = one tag per company: `Revenues` or `RevenueFromContractWithCustomerExcludingAssessedTax`, whichever reaches the latest fiscal year (tie: more years, then `Revenues`) | Never mix definitions within a series. Choice + reason in `edgar_coverage.csv`. |
| ~365-day period = fiscal year, ~91 days = quarter, 6/9-month YTD ignored; FY label = year the period ends (minus 7 days for 52/53-week years) | TDG's year ends in September; LHX's on the Friday nearest Dec 31 |
| Keep the **first** filing that reported a period; a later different value is a `revised_*` column | Each 10-K repeats last year; the first filing is what investors knew then. Keeping the latest would break as-of matching |
| As-of = latest period whose filing date ≤ award announcement date | No look-ahead: a June 30 quarter isn't public until the 10-Q in late July |
| Percentages only for **definite** awards | A ceiling isn't money |
| Gaps (missing tag, skipped periods, tag dropped in newer filings) are listed, never filled | |

**Coverage (Nov 2026).** TDG and HWM don't tag RPO. GD tags RPO only at fiscal year end
(10-K). LHX skips RPO for 2020 and in its Q3 2026 10-Q, and its FY2025 10-K tags the
year's revenue to a 3-month period, so FY2025 revenue shows as missing. RTX's chosen
revenue tag starts in FY2021. GE's figures change with the HealthCare (2023) and Vernova
(2024) spin-offs; recasts appear as revisions.

SEC etiquette: 12 requests per run, 2 seconds apart (the limit is 10 per second),
User-Agent with the `HANGAR_CONTACT` email. The SEC refuses requests without an email.
If it is unreachable, the previous figures are kept.

## Phase 3: timestamped pitches (pipeline/pitches.py, pitches/)

How to write one: `pitches/README.md` (copy `pitches/_TEMPLATE.md`).

```
pitches/<date>-<TICKER>-<long|short>.md   TOML header (+++) + Markdown text
        │  push to main (triggers the workflow) or wait for the daily run
pitches.py record ──► data/pitch_ledger.csv: SHA-256 per part, first_seen date
        │  check(): published parts unchanged, none removed; dates ≤ 3 days before first_seen
site.py           ──► site/pitches.html, site/pitches/<slug>.html ("What happened since" from Hangar data)
        │  after deploy
pitches.py archive ─► Internet Archive "Save Page Now" + snapshot link back into the ledger
```

| Rule | Why |
|---|---|
| Parts = original pitch, then each `## Update YYYY-MM-DD` / `## Closed YYYY-MM-DD` note | Thoughts can be added, never rewritten |
| Fingerprint = SHA-256 of the text (line endings and trailing spaces normalized) | One changed character = different fingerprint |
| An edited, removed or deleted published part fails `tests/test_pitches.py` and stops the build | Nothing is published until the text is restored |
| A part's date may be at most 3 days before the run that first records it; never in the future | No backdating |
| No returns shown; Yahoo link from the pitch date | Price-data terms (see Phase 1) |
| Markdown: a small built-in subset; HTML is escaped, only http(s) links | No new dependency; a pitch can't inject code |

## Data sources & etiquette

- **war.gov** blocks automated readers on its article pages (HTTP 403). We do not
  work around that. We read its open RSS feed for the list of pages and the
  Internet Archive's saved copies for the text. Every record links to both.
- Every request sends an honest User-Agent naming the project. The contact
  comes from the `HANGAR_CONTACT` environment variable (a GitHub secret), never
  from the code.
- Requests to each host are at least 2 seconds apart. A 403 stops the run
  instead of retrying.

## Parsing rules (pipeline/parse.py)

| Source pattern | What we store |
|---|---|
| One company, one amount | `kind=award`, `amount_usd` |
| Several companies "will compete for" / "shared" / "combined" $X | `kind=shared_award`, `amount_usd` empty, `shared_amount_usd=X` on each (prevents double counting) |
| Several companies, each "(contract no., $X)" | one `award` per company with its own amount |
| "UPDATE: ... added as an awardee" | `kind=added_awardee` |
| "CORRECTION: ..." | `kind=correction`, never counted in totals |
| Two awards run together in one paragraph | split after "...is the contracting activity." |
| Typo'd figure such as `$42,758,8967` | not read; record flagged for review |

Other fields: modification vs. new contract vs. order, contract number (with its
type from the 9th character: C contract, D IDIQ, F order, G agreement, R solicitation),
total cumulative face value, value if all options are exercised, money obligated
at award, the "(Awarded <date>)" date when it differs from the announcement date,
IDIQ and Foreign Military Sales flags, and small-business status.

**Face value ≠ revenue.** An IDIQ "maximum $957M" is a ceiling. Little or none of it
may ever be ordered. `obligated_at_award_usd` is the money actually committed today.

## Ticker mapping (pipeline/tickers.py, data/ticker_aliases.csv)

Rules are regexes on a normalized name ("The Boeing Co.,*" -> "boeing co"),
checked in file order, each with optional `valid_from` / `valid_to` dates because
ownership changes (Amentum was private until Sept 2024; Aerojet joined L3Harris
in July 2023). Statuses: `public`, `private`, `foreign_listed`, `joint_venture`
(never auto-mapped, since a JV's revenue is split between its owners), or
`unmapped`, which lands in `data/unmapped_companies.csv` for review.

## Data quality: what we found in 2024–2026 and how it's handled

| Issue in the source | Handling |
|---|---|
| Same paragraph on two days (e.g. Sept 29, 2026 repeats 55 of Sept 28's paragraphs) | later copy -> `kind=repeat`, never counted twice |
| One company named, but the amount is a shared multiple-award ceiling (e.g. "$55,000,000,000 multiple award … concurrently with other awardees") | `kind=shared_award`, not credited to the company |
| Ceilings much larger than real money (Boeing $131B ceiling IDIQ) | `amount_qualifier` + `is_idiq`; the site must label ceilings |
| Typos: `$42,758,8967`, "Northrup", "Sterling Virginia" | malformed numbers never read; punctuation repaired only when unambiguous, with a note |
| Amount only in a later sentence ("The total value of this contract is $X") | read only in fixed phrasings, qualifier kept, note added |
| "ceiling from $2.6B to $3.0B" | the "from" figure is never taken as the award |
| "USTRANSCOM **has awarded** … to X" (agency as subject) | not parsed as a company; flagged |
| "This contract was awarded Dec. 19, 2025." as its own sentence/paragraph | glued to the award above, not a new award |
| No dollar amount at all | record kept, flagged `review_reasons` |

`review_reasons` = the headline company or amount may be wrong or missing (≈2% of records).
`notes` = secondary info missing or repaired; headline numbers are fine.

**Coverage gaps (2024-01 to 2026-11).** No archived page exists for 42 business days.
29 of them are Oct 1 – Nov 10, 2025; pages resume on Nov 12, 2025, the day the
2025 government shutdown ended, which suggests announcements paused (not
independently confirmed). Others fall on executive-order closure days (Dec 24 etc.).
The site shows these days as "no announcement found", never as zero awards.

## Data files

| File | What it is |
|---|---|
| `data/raw/<year>/<article id>.html.gz` | the archived page exactly as downloaded |
| `data/pages.csv` | every known daily page and whether we have it |
| `data/awards.csv` | one row per award, with ticker columns |
| `data/coverage.csv` | every business day: do we have its announcement? |
| `data/page_report.csv` | records per page and any page-level problem |
| `data/unmapped_companies.csv` | companies with no ticker rule, largest first |
| `data/financials.csv` | RPO balances and fiscal-year revenue from SEC filings, each with accession, form, filed date, filing link, and any later revision |
| `data/edgar_coverage.csv` | per company and metric: status, tag used and why, period range, gaps |
| `data/edgar_companies.csv` | ticker → SEC CIK |

## awards.csv columns

| Column | Meaning |
|---|---|
| `announcement_date` | date of the daily war.gov page (awards are posted ~5 p.m. ET, after the market close) |
| `award_date` | "(Awarded Oct. 28, 2026)" date when the text gives one, else the announcement date |
| `branch` | heading the award sits under (ARMY, NAVY, AIR FORCE, DEFENSE LOGISTICS AGENCY, …) |
| `source_url` / `archive_url` | official war.gov page / the Internet Archive copy we parsed |
| `paragraph_index`, `text` | position on the page and the **exact source text** of this award |
| `kind` | `award`, `shared_award` (pool split among companies, not credited to any one), `added_awardee`, `correction`, `repeat` |
| `company`, `city`, `state_or_country`, `small_business` | as written in the source (typos included) |
| `amount_usd` | this company's headline dollar figure; empty for shared pools |
| `amount_qualifier` | `maximum`, `ceiling`, `estimated`, `including options`, or empty |
| `shared_amount_usd`, `shared_with_count` | the pool and how many companies are listed (empty count = others announced on other days) |
| `cumulative_face_value_usd` | contract total after this action ("brings the total cumulative face value to…") |
| `value_with_options_usd` | value if all options are exercised |
| `obligated_at_award_usd` | money actually committed on the award date (often a small fraction of the headline) |
| `action_type` | `contract`, `modification`, `order` |
| `contract_number`, `contract_number_type`, `modification_number`, `all_contract_numbers` | PIIDs; type comes from the 9th character |
| `is_idiq`, `involves_fms` | indefinite-delivery contract; Foreign Military Sales |
| `review_reasons` | headline company/amount may be wrong or missing |
| `notes` | secondary info missing, repaired, or taken from a later sentence |
| `ticker`, `ticker_parent`, `ticker_rule`, `ticker_status` | mapping result and the rule that produced it |

**Which amount to sum for a company:** `amount_usd` where `kind` is `award` or
`added_awardee`, and treat rows with a `maximum`/`ceiling` qualifier or `is_idiq`
as *potential* value, not money awarded.

## The website (pipeline/site.py, pipeline/metrics.py, site_src/)

```
data/*.csv ──► metrics.py (what counts) ──► Jinja2 templates ──► site/ (plain HTML + CSS)
```

- **No JavaScript, no server, no database.** GitHub Pages serves the files in `site/`.
  Charts are SVG drawn in Python; tooltips are SVG `<title>` elements.
- **Counting rule** (metrics.py): *definite* = stated award amounts; *potential* =
  ceilings, maximums, estimates and new IDIQ contracts; *excluded* = shared pools,
  repeats, corrections, records needing review. Never added together.
- **Every amount is a link** to the archived source page with a text fragment
  (`#:~:text=10,309,201`), so the browser scrolls to and highlights that figure.
- **Phones:** award tables become stacked cards below 680px; no sideways scrolling.
  Light/dark theme follows the device setting.
- **Page weight:** older years are on their own pages (`company/LMT-2025.html`).
- **Cache-busting:** the stylesheet URL carries a hash of its contents (`style.css?v=…`).
- `tests/test_site.py` checks every internal link, the disclaimer on every page,
  and that every dollar amount links to a source.

## Running it

```bash
python -m pytest -q                                      # tests (stdlib only + pytest)
python -m pipeline.run backfill --years 2024 2025 2026   # one-time history download
python -m pipeline.run daily                             # new pages (GitHub Actions runs this)
python -m pipeline.run build                             # re-parse cached pages only
python -m pipeline.edgar daily                           # SEC backlog/revenue (needs HANGAR_CONTACT)
python -m pipeline.edgar build                           # re-extract from cached SEC files only
python -m pipeline.site                                  # build the website into site/
python -m http.server 8765 --directory site              # preview at http://localhost:8765
```
