# Pitches

1. Copy `_TEMPLATE.md` to a new file named `<date>-<TICKER>-<long|short>.md`,
   e.g. `2026-11-15-KTOS-long.md`. Use the same date, ticker and direction in the header.
2. Write the pitch, including an entry price, a valuation check against peers and a
   price target (see the template). Prices are ones you look up and type in yourself. Supported formatting: `##` headings, paragraphs, `-` lists,
   `1.` lists, `> quotes`, **bold**, *italic*, `code`, and [links](https://...).
3. Commit and push (or open a pull request). The next daily run fingerprints it
   in `data/pitch_ledger.csv`, publishes it, and asks the Internet Archive to save a copy.
   The date in the header may be at most 3 days before that run.

**After publishing, never edit the text.** The tests compare every published part
with its fingerprint and stop the site from updating if anything changed.
To add a thought, append at the bottom:

    ## Update 2027-02-03

    What changed and what you think now.

To end the call (nothing may follow it):

    ## Closed 2027-11-15

    How it turned out, honestly, including what you got wrong.

## Scoring a pitch (about once a month)

Returns are calculated from closing prices you type in; no code downloads prices.

1. Pick one trading day (for example the first Friday of the month).
2. Look up that day's closing price for each **open** pitch's stock and for **SPY**.
   Prefer the company's own investor-relations "stock quote / historical price" page.
3. Add one line per price to `data/pitch_marks.csv`:

       date,ticker,close_usd,source,source_url
       2026-12-04,KTOS,38.61,Kratos investor relations,https://ir.kratosdefense.com/
       2026-12-04,SPY,750.00,State Street SPY page,https://www.ssga.com/

   Every date needs a SPY line, or that date is not scored. `source_url` may be empty.
4. Run `python -m pytest -q`. A weekend date, a future date, a duplicate or a
   non-number fails with the line number.

A new pitch needs two lines for its entry day (the stock and SPY): the last close
on or before the pitch date, the same price the pitch text states.
