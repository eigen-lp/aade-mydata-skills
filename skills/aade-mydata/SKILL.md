---
name: aade-mydata
description: Read a Greek business's AADE myDATA e-books (invoices received and issued, income and expense totals), propose and send expense classifications (χαρακτηρισμοί εξόδων, E3 and VAT codes), and reconcile myDATA against ERP, accounting or bank CSV exports. Use when the user mentions myDATA, ΑΑΔΕ, ηλεκτρονικά βιβλία, MARK/ΜΑΡΚ, χαρακτηρισμός εξόδων, E3 codes, or matching invoices against their books.
license: MIT
compatibility: Python 3.9+ (standard library only) and network access to mydataapidev.aade.gr or mydatapi.aade.gr.
allowed-tools:
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py --help)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py check *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py docs *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py transmitted *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py income *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py expenses *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py classify *)
  - Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py reconcile *)
metadata:
  version: 0.3.1
  spec: myDATA REST API v2.0.2 (ERP channel)
  homepage: https://github.com/eigen-lp/aade-mydata-skills
---

# AADE myDATA

Everything goes through `python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py <command>` (stdlib Python, JSON/CSV out). Default environment is **test**. Run each command on its own, exactly in that form: no `cd`, `&&`, `;`, pipes, `source` or `>` redirects. Save output with `--out <file>` instead, and load a credentials file with `--env-file <path>` when the user names one.

**Script path.** If `${CLAUDE_SKILL_DIR}` shows up literally instead of as a real path (some apps don't substitute it), use the absolute path of `scripts/mydata.py` in the folder that contains this SKILL.md. Find it once and reuse it.

**Cowork and other sandboxes.** The script needs `python3` and HTTPS access to `mydatapi.aade.gr` (production) or `mydataapidev.aade.gr` (test). If a call fails with a network error, tell the user to allow those domains (see the Cowork section of [setup.md](references/setup.md)). If `check` reports the credentials as NOT SET there, ask the user for the path of their `.env` file in a folder they've shared, and pass it with `--env-file`.

## Reads vs writes

- **Reads: just run them.** `check`, `docs`, `transmitted`, `income`, `expenses`, `reconcile` and `classify` (a dry run) never change anything in myDATA. Run them without asking the user first.
- **The only write is `send`** (SendExpensesClassification). It is deliberately not pre-approved, so Claude Code asks the user before it runs. Before you even propose it:
  1. Run `classify` and show the user the table it prints and the target environment (test or PRODUCTION).
  2. Ask for an explicit yes to that exact table, and wait for it. A yes covers that table only.
  3. Run the `send` command `classify` printed, with its `--confirm` code. The code fingerprints the environment and XML, so `send` refuses if anything changed since the dry run; then dry-run again and ask again. Never compute or reuse a code yourself.
- Production also needs `--yes-production` on `send`. Only use it when the user has said this is for their real books.

## Other rules

1. **Never ask for keys in chat.** Credentials come from the plugin's settings (Claude Code asks for them when the plugin is enabled and keeps the key in the system's secure store), from `MYDATA_USER_ID` / `MYDATA_SUBSCRIPTION_KEY` (and `MYDATA_ENV`) in the environment, or from an `--env-file`. If `check` says they're missing, tell the user to set them with `/plugin` → aade-mydata → configure, and point to [references/setup.md](references/setup.md). If the user pastes a key anyway, don't repeat it and suggest rotating it.
2. **The accountant decides.** Present classifications as proposals and recommend the business's accountant (λογιστής) reviews them, especially for production. Not tax advice; not affiliated with AADE.
3. Never use `transactionMode` (reject/deviation) unless the user explicitly asks to reject or dispute a specific invoice.
4. Show summaries and counts, not raw dumps of the user's books, unless asked. Save large output with `--out`.

## Commands

| Task | Command (prefix: `python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py`) | Read first |
|---|---|---|
| Check setup | `check [--env-file .env.prod]` | [setup.md](references/setup.md) |
| Expense documents (received + own 13.x/14.x) | `docs --include-self-entered --from 2026-10-01 --to 2026-10-31 --format summary` | [books.md](references/books.md) |
| Documents you issued | `transmitted --from ... --to ... --format summary` | [books.md](references/books.md) |
| Income / expense book totals | `income\|expenses --from ... --to ... [--counterpart AFM] [--format csv]` | [books.md](references/books.md) |
| Validate a classification proposal (dry run) | `classify --file proposal.json` | [classify.md](references/classify.md) |
| **Write** the approved classification | `send --file proposal.json --confirm <code> [--yes-production]` | [classify.md](references/classify.md) |
| Reconcile with an ERP/bank CSV | `reconcile --from ... --to ... --file erp.csv` | [reconcile.md](references/reconcile.md) |
| Endpoints, fields, errors | | [api.md](references/api.md) |

All commands take `--env test|prod`, `--env-file PATH`, `--out PATH`, and `--entity <client AFM>` for accountants acting for a client.

## Classification loop

1. `docs --include-self-entered --format summary` for the period (the expense book view: no self-billed income, no delivery notes); pick invoices with `classified: no` that aren't cancelled.
2. Read the full invoices (`docs --out docs.json`, then read the file) and draft a proposal JSON following [classify.md](references/classify.md). Choose codes from `references/expense-combinations.json` for each invoice type; explain each choice in one line.
3. `classify --file proposal.json`. Fix any `error:` lines and re-run.
4. Show the table and environment, get an explicit yes, then run the printed `send ... --confirm <code>` command. Report each `statusCode` and `classificationMark`.
