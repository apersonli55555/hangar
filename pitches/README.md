# Pitches

1. Copy `_TEMPLATE.md` to a new file named `<date>-<TICKER>-<long|short>.md`,
   e.g. `2026-11-15-KTOS-long.md`. Use the same date, ticker and direction in the header.
2. Write the pitch. Supported formatting: `##` headings, paragraphs, `-` lists,
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
