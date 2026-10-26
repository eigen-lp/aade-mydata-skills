# Reading the books

All read commands are safe: GET only, no changes in myDATA. Output is JSON on stdout (or CSV with `--format csv`); save it with `> file.json` when you want to reuse it.

## Which command

| Question | Command |
|---|---|
| Which expense invoices do I have this month? Which are unclassified? | `docs --include-self-entered --from 2026-10-01 --to 2026-10-31 --format summary` |
| Full detail of received invoices (lines, VAT, classifications) | `docs --from ... --to ... > docs.json` |
| What did I (or my provider) transmit? | `transmitted --from ... --to ...` |
| Expense / income totals per supplier, date and invoice type | `expenses --from ... --to ...`, `income --from ... --to ...` |
| Only one counterparty | add `--counterpart <AFM>` |
| Only one invoice type | add `--inv-type 1.1` (types in the table below) |
| Incremental fetch since last run | `docs --mark <highest MARK seen last time>` |

`docs` is RequestDocs: documents **other parties** submitted that concern you (your purchases, plus their classifications and cancellations). `transmitted` is RequestTransmittedDocs: what **you** submitted.

Recipient-entered documents (types 13.x, 14.x, 15.1, 16.1, e.g. a foreign SaaS bill entered as 14.4) are expenses that **you** transmit, so AADE returns them from RequestTransmittedDocs, not RequestDocs. `docs --include-self-entered` makes both calls and adds those types, so you get every expense document in one list. `reconcile --side expenses` always does this.

## `--format summary` / `csv` columns

`mark, issueDate, invoiceType, series, aa, issuerVat, issuerName, counterpartVat, counterpartName, netValue, vatAmount, grossValue, cancelledByMark, classified`

`classified` is `yes` when the invoice carries expense classifications (line or summary level) or a separately sent classification for its MARK is in `expensesClassifications`. A new submission replaces the old one, so check before re-classifying.

A non-empty `cancelledByMark` means the invoice was cancelled; don't classify or count it.

## Common invoice types (spec table 8.1)

| Type | Meaning |
|---|---|
| 1.1 | Τιμολόγιο Πώλησης (sales invoice) |
| 1.2 / 1.3 | Sales invoice, intra-EU / third-country deliveries |
| 2.1 | Τιμολόγιο Παροχής Υπηρεσιών (services invoice) |
| 2.2 / 2.3 | Services, intra-EU / third country |
| 5.1 / 5.2 | Πιστωτικό τιμολόγιο, correlated / not correlated (credit note) |
| 11.1 / 11.2 | ΑΛΠ / ΑΠΥ retail receipts |
| 13.x | Expenses from domestic/foreign retail documents entered by the recipient |
| 14.x | Documents entered by the recipient (intra-EU acquisitions, foreign services, ΕΦΚΑ...) |
| 16.1 | Rent (expense) |
| 17.x | Accounting adjustment entries (payroll, depreciation, other) |

The full list, with which expense classifications each type accepts, is in `expense-combinations.json`.

## Reading amounts

- `netValue` net, `vatAmount` VAT, `grossValue` total; withheld tax, fees, stamp duty, other taxes and deductions are separate fields.
- Credit notes (5.x, 11.4, 13.31, 14.31) are returned with positive amounts; treat them as negative in totals.
- Amounts are strings in JSON to keep exact decimals.
