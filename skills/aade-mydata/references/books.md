# Reading the books

All read commands are safe: GET only, no changes in myDATA. Output is JSON on stdout (or CSV with `--format csv`); save it with `--out file.json` when you want to reuse it (the file is created readable only by you).

## Which command

| Question | Command |
|---|---|
| Which expense invoices do I have this month? Which are unclassified? | `docs --include-self-entered --from 2026-10-01 --to 2026-10-31 --format summary` |
| Full detail of received invoices (lines, VAT, classifications) | `docs --from ... --to ... --out docs.json` |
| What did I (or my provider) transmit? | `transmitted --from ... --to ...` |
| Expense / income totals per supplier, date and invoice type | `expenses --from ... --to ...`, `income --from ... --to ...` |
| Only one counterparty | add `--counterpart <AFM>` |
| Only one invoice type | add `--inv-type 1.1` (types in the table below) |
| Incremental fetch since last run | `docs --mark <highest MARK seen last time>` |

`docs` is RequestDocs: documents **other parties** submitted that concern you (your purchases, plus their classifications and cancellations). `transmitted` is RequestTransmittedDocs: what **you** submitted.

Which book a document belongs to doesn't follow the endpoint one-to-one. `docs --include-self-entered` and `reconcile` apply the same rule as AADE's own books (checked against `expenses`/`income` totals on production data):

| Source | Document | Book |
|---|---|---|
| either | delivery notes 9.1, 9.2, 9.3, 10.1, 10.2 | none (no value; skipped) |
| `docs` (RequestDocs) | `selfPricing` = true (αυτοτιμολόγηση: the customer issued it for you) | **income** |
| `docs` | everything else | expenses |
| `transmitted` (RequestTransmittedDocs) | recipient-entered 13.x, 14.x, 15.1, 16.1 (e.g. a foreign SaaS bill as 14.4) | expenses |
| `transmitted` | everything else | income |

So `docs --include-self-entered` is the **expense book view**: received documents plus your own 13.x/14.x entries, minus self-billed income and delivery notes. Its `excluded` field counts what was dropped. Plain `docs` returns RequestDocs unfiltered; the `selfPricing` column marks self-billed income there. Never propose expense classifications for self-billed or delivery-note documents.

## `--format summary` / `csv` columns

`mark, issueDate, invoiceType, series, aa, issuerVat, issuerName, counterpartVat, counterpartName, netValue, vatAmount, grossValue, cancelledByMark, selfPricing, classified`

`classified` is `yes` when the invoice carries expense classifications (line or summary level) or a separately sent classification for its MARK is in `expensesClassifications`. A new submission replaces the old one, so check before re-classifying.

A non-empty `cancelledByMark` means the invoice was cancelled; don't classify or count it.

**Re-sent invoices.** When a supplier transmits the same invoice again (same issuer, date, branch, type, series and number, hence the same `uid`), it gets a new MARK and AADE keeps only the latest as valid (spec §4.1.1). RequestDocs still returns both copies, without `cancelledByMark`. `docs` and `transmitted` keep the highest MARK per `uid` and list the dropped copies under `superseded` (`mark`, `supersededBy`, `uid`). Copies that were explicitly cancelled (`cancelledByMark`) are not treated as re-sends; they stay visible as cancelled. These are not double charges; mention them to the user only if they ask or if the old MARK was already booked or classified.

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
- Credit notes (5.x, 11.4, 13.31, 14.31): `docs`/`transmitted` return them with **positive** amounts (the XSD allows no negatives), so subtract them yourself. The `income`/`expenses` book rows already carry them as **negative** values. Don't negate twice when comparing the two.
- The `expenses` book (RequestMyExpenses) leaves out recipient-entered retail documents 13.x, which `docs --include-self-entered` does list (seen on production). Expect the invoice-level total to exceed the book by those amounts.
- When comparing invoice-level reads with the books, exclude cancelled documents (`cancelledByMark`) and apply the book rule above.
- Amounts are strings in JSON to keep exact decimals.
