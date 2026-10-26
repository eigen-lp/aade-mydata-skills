# aade-mydata-skills

[Ελληνικά](README.el.md)

Agent Skills that let [Claude Code](https://claude.com/claude-code) work with **AADE myDATA**, the Greek tax authority's electronic books:

- **Read your books**: invoices suppliers sent you, invoices you issued, income and expense totals per counterparty, with automatic paging. JSON or CSV.
- **Classify expenses**: Claude drafts the χαρακτηρισμοί εξόδων (E3 and VAT codes) for unclassified invoices, checks them against AADE's official combinations table, shows you exactly what will be sent, and sends only after you say yes.
- **Reconcile**: match myDATA against an ERP, accounting or bank CSV export on MARK, AFM, date and amount, and list what's missing or different on each side.

Built for Greek businesses and accountants. Version 1 reads and classifies; it does not issue or cancel invoices. It talks to the **test environment by default**.

> Not affiliated with or endorsed by AADE. Not tax advice: have your accountant review classifications before they go to production.

## Install

**1. Add the skill** (either way):

```bash
# as a Claude Code plugin
/plugin marketplace add eigen-lp/aade-mydata-skills
/plugin install aade-mydata@eigen-lp
```

```bash
# or copy the skill folder
git clone https://github.com/eigen-lp/aade-mydata-skills
cp -r aade-mydata-skills/skills/aade-mydata ~/.claude/skills/
```

**2. Get test credentials** at <https://mydata-dev-register.azurewebsites.net> and put them in your shell, not in the chat:

```bash
export MYDATA_USER_ID="your-test-username"
export MYDATA_SUBSCRIPTION_KEY="your-test-subscription-key"
```

**3. Start Claude Code from that shell** and ask, for example:

- "Check my myDATA setup."
- "Which supplier invoices from October are still unclassified? Propose classifications."
- "Reconcile October purchases in myDATA with `purchases.csv`."

Requirements: Python 3.9 or newer (standard library only, nothing to `pip install`).

## Production

Production uses separate credentials, created after logging in with TAXISnet at <https://www1.aade.gr/saadeapps2/bookkeeper-web> («Φόρμα εγγραφής στο myDATA REST API»). Set `MYDATA_ENV=prod` or pass `--env prod`. Reading works the same; sending classifications to production additionally needs `--yes-production` on `send`, which Claude adds only when you explicitly confirm that the data is for your real books. You can keep each environment's credentials in a git-ignored file and pass it with `--env-file .env.prod`. Accountants acting for a client add `--entity <client AFM>`.

## Safety

- Keys stay in environment variables. The script never prints them, and the skill tells Claude never to ask for them in chat.
- **Reads run without asking; writes always ask.** The skill pre-approves its read commands (`check`, `docs`, `transmitted`, `income`, `expenses`, `classify`, `reconcile`), so Claude runs them straight away. The only write, `send`, isn't pre-approved: Claude Code shows you the command and asks before it runs.
- `classify` is a dry run: it validates the proposal (codes, amounts, AADE's type/category/E3 combinations) and prints a table, the exact XML and a confirmation code. Claude must show you that table and get an explicit yes; `send` only accepts the matching code, so what goes to AADE is exactly what you approved.
- Production writes need the `--yes-production` flag.
- Re-sending a classification for the same invoice replaces the previous one in myDATA.

More in [SECURITY.md](SECURITY.md).

## Using the script directly

```bash
S=skills/aade-mydata/scripts/mydata.py
python3 $S check
python3 $S docs --from 2026-10-01 --to 2026-10-31 --format summary
python3 $S expenses --from 2026-10-01 --to 2026-10-31 --format csv
python3 $S classify --file proposal.json                     # dry run, prints a confirmation code
python3 $S send --file proposal.json --confirm <code>        # the only write (test environment)
python3 $S docs --env-file .env.prod --from 2026-01-01 --to 2026-10-31 --out docs.json
python3 $S reconcile --from 2026-10-01 --to 2026-10-31 --file purchases.csv
```

See `skills/aade-mydata/references/` for the proposal format, CSV columns and API notes.

## Sources

Based on the official myDATA REST API technical description for ERP users **v2.0.2** and its XSDs, and AADE's characterization-combinations spreadsheet v2.0.2, all published at <https://www.aade.gr/en/mydata/technical-specifications-versions-mydata>. AADE's files are not redistributed here; `tools/build_combinations.py` rebuilds the combinations table from the official spreadsheet.

## Development

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

Tests are offline. `tools/seed_test_invoices.py` (dev only, hard-wired to the test environment) sends a couple of synthetic 13.1/14.4 expense documents for your test AFM (`MYDATA_TEST_AFM`) so you can try `classify` and `reconcile` end to end. To also validate the generated XML against AADE's schema, download the v2.0.2 XSD zip from the page above, unzip it, and run with `AADE_XSD_DIR=/path/to/xsds` (needs `xmllint`).

## Licence and contact

MIT, © EIGEN LAB Ε.Ε. Made by [Eigen](https://eigen.gr), an AI engineering consultancy in Athens. Need this wired into your ERP or month-end process? Get in touch at [eigen.gr](https://eigen.gr).
