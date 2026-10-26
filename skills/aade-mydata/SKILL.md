---
name: aade-mydata
description: Read a Greek business's AADE myDATA e-books (invoices received and issued, income and expense totals), propose and send expense classifications (χαρακτηρισμοί εξόδων, E3 and VAT codes), and reconcile myDATA against ERP, accounting or bank CSV exports. Use when the user mentions myDATA, ΑΑΔΕ, ηλεκτρονικά βιβλία, MARK/ΜΑΡΚ, χαρακτηρισμός εξόδων, E3 codes, or matching invoices against their books.
license: MIT
compatibility: Python 3.9+ (standard library only) and network access to mydataapidev.aade.gr or mydatapi.aade.gr.
metadata:
  version: 0.1.0
  spec: myDATA REST API v2.0.2 (ERP channel)
  homepage: https://github.com/eigen-lp/aade-mydata-skills
---

# AADE myDATA

Everything goes through `${CLAUDE_SKILL_DIR}/scripts/mydata.py` (stdlib Python, JSON/CSV out). Run `python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py --help` for flags. Default environment is **test**.

## Safety rules (always)

1. **Never ask for keys in chat.** Credentials live in environment variables `MYDATA_USER_ID` and `MYDATA_SUBSCRIPTION_KEY` (plus optional `MYDATA_ENV`). If they are missing, tell the user to set them in their shell and point to [references/setup.md](references/setup.md). If the user pastes a key anyway, don't repeat it and tell them to rotate it.
2. **Read freely, write only after an explicit yes.** Before any `--send`, run the dry run, show the user the table it prints (MARK, type, category, amount, VAT) and the target environment, and wait for a clear "yes" to that exact table. A yes covers that table only; any change means a new dry run and a new yes.
3. **Production needs the flag.** Sending to production needs `--env prod --send --yes-production`. Only add `--yes-production` when the user explicitly says this is for the real books.
4. **The accountant decides.** Present classifications as proposals. Recommend that the business's accountant (λογιστής) reviews them before sending, especially in production. This is not tax advice, and the tool is not affiliated with AADE.
5. Never use `transactionMode` (reject/deviation) unless the user explicitly asks to reject or dispute a specific invoice.

## Workflows

| Task | Command | Read first |
|---|---|---|
| Check setup | `mydata.py check` | [setup.md](references/setup.md) |
| List invoices received (expenses) | `mydata.py docs --from 2026-10-01 --to 2026-10-31 --format summary` | [books.md](references/books.md) |
| List invoices you issued | `mydata.py transmitted --from ... --to ...` | [books.md](references/books.md) |
| Income / expense book totals | `mydata.py income\|expenses --from ... --to ... [--counterpart AFM] [--format csv]` | [books.md](references/books.md) |
| Propose and send expense classifications | `mydata.py classify --file proposal.json` then `--send` | [classify.md](references/classify.md) |
| Reconcile with ERP/bank CSV | `mydata.py reconcile --from ... --to ... --file erp.csv` | [reconcile.md](references/reconcile.md) |
| Endpoints, fields, errors | | [api.md](references/api.md) |

Accountants acting for a client add `--entity <client AFM>` to any command.

## Classification loop (short version)

1. `docs --format summary` for the period; pick invoices with `classified: no`.
2. For each, get the full invoice (`docs --format json`, filter by MARK) and draft a proposal JSON following [classify.md](references/classify.md). Choose codes from `references/expense-combinations.json` for that invoice type; explain each choice in one line.
3. `classify --file proposal.json` (dry run). Fix any `error:` lines.
4. Show the table, say which environment, ask for an explicit yes, then `--send`. Report each `statusCode` and `classificationMark`.
