# Reviewing the contracts data

About 30–45 minutes. You need Excel (or Google Sheets) and a browser.

## 1. The audit sample: `data/audit_sample.csv`

44 records, drawn at random from 8 groups (plain awards, your 11 tickers, shared
pools, amounts read from a later sentence, repeats, flagged records, and the 5
largest amounts). The draw uses a fixed seed, so rerunning
`python -m scripts.audit_sample` gives the same 44.

For each row, open `archive_url`, press Ctrl+F, paste the first few words of
`text`, and check:

| Column | Question |
|---|---|
| `company` | Is this the company the award went to (not the agency, not a city)? |
| `amount_usd` | Is it the figure for **this** company's award, word for word? Not an old value ("from $X"), not an obligated amount, not another company's? |
| `amount_qualifier` | If the source says "maximum", "ceiling" or "estimated", does the qualifier say so too? |
| `kind` = `shared_award` | Does the source really say the money is split among companies? (`shared_amount_usd` is the pool) |
| `kind` = `repeat` | Does the same paragraph really appear on the earlier date named in `notes`? |
| `ticker` | Is the company really owned by that parent **on that date**? |
| `review_reasons` | If flagged, is the reason fair, and is the headline left empty rather than guessed? |

Write `Y` or `N` in `correct_Y_N`, plus a comment for every `N`.
Send me the `N` rows: each one becomes a test before it's fixed.

**What would worry me:** any wrong `amount_usd` on an `award` row (that number
feeds totals and price reactions). Wrong city or missing contract number is minor.

## 2. Large amounts held for confirmation

Every single amount of $10B+ that isn't stated as a ceiling is flagged
"confirm against the source" and kept out of default totals. List them with:

```
python -m scripts.review --top 0
```

For each one you've checked against its source page, add a line to
`data/reviewed.csv`:

```
announcement_date,company,amount_usd,verdict,reviewed_on,comment
2026-07-30,Lockheed Martin Corp.,53859843289.0,confirmed,2026-11-09,PAC-3 MSE multiyear; matches source
```

- `confirmed` = the number is exactly what the source says. The flag clears.
- Any other verdict (e.g. `source_typo_suspected`) keeps the flag, and your comment is shown.
- Copy `company` and `amount_usd` exactly as they appear in `data/awards.csv`.
- We never edit a number to "fix" the government's typo. We only label it.

Then run `python -m pipeline.run build`.

## 3. Ticker rules marked "(verify)"

In `data/ticker_aliases.csv`, rows whose `evidence` says "(verify)" came from
general knowledge, not a checked document. Each should be checked against the
parent's latest 10-K, Exhibit 21 (list of subsidiaries) on SEC EDGAR. Once the
project email is set up, I can automate this.
