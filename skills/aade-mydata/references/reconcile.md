# Reconciling myDATA with ERP or bank exports

`reconcile` compares the invoices AADE holds with a CSV exported from the user's ERP, accounting package or bank, and lists what is missing or different on either side. Read-only.

```bash
# expenses side (default): the expense book (see books.md): received documents plus your own
# 13.x/14.x/15.1/16.1 entries, without self-billed income and delivery notes
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py reconcile --from 2026-10-01 --to 2026-10-31 --file purchases.csv

# income side: the income book: documents you issued plus self-billed ones a customer issued for you
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py reconcile --side income --from ... --to ... --file sales.csv

# offline, against saved JSON (full format, not --format summary); --mydata-json is repeatable
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py docs --from ... --to ... --out docs.json
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py transmitted --from ... --to ... --out tx.json
python3 ${CLAUDE_SKILL_DIR}/scripts/mydata.py reconcile --file purchases.csv --mydata-json docs.json --mydata-json tx.json
```

## CSV format

Comma, semicolon or tab separated; UTF-8 (a BOM from Excel is fine). Header names are matched case-insensitively, Greek or English:

| Meaning | Accepted headers |
|---|---|
| MARK | `mark`, `ΜΑΡΚ`, `invoice_mark` |
| Counterparty AFM | `afm`, `ΑΦΜ`, `vat`, `vat_number`, `counterpart_vat`, `supplier_vat`, `customer_vat`, `tin` |
| Issue date | `date`, `issue_date`, `Ημερομηνία` (YYYY-MM-DD or DD/MM/YYYY) |
| Gross amount | `amount`, `gross`, `total`, `Σύνολο`, `Ποσό` (1.234,56 and 1,234.56 both work) |
| Reference (optional) | `ref`, `number`, `doc_no`, `Αριθμός`, `Παραστατικό` |

Required: an amount column, plus either a MARK column or AFM + date columns. If the export has other names, rename the headers (or write a small converted CSV) before running.

Amounts are compared as absolute values, so credit notes exported as negatives still match. Default tolerance is €0.01 (`--tolerance`).

## Matching

1. Rows with a MARK are matched to the myDATA invoice with that MARK. Then amount, AFM and date are compared; any difference puts the pair in `mismatched` with the reasons.
2. Rows without a MARK (or whose MARK isn't in myDATA) are matched on **AFM + issue date + gross amount**, only if exactly one unmatched myDATA invoice fits. A row whose MARK differs from the one found this way is `mismatched`.
3. Whatever is left: ERP rows go to `missingInMyData`, myDATA invoices to `missingInErp`. Cancelled myDATA invoices (with `cancelledByMark`) that no ERP row claims go to `cancelledInMyData` instead.

Both live sides call RequestDocs and RequestTransmittedDocs and keep the documents of that book (rule in [books.md](books.md)); saved `--mydata-json` files are filtered the same way using their `method`. The AFM compared is the other party's: the **issuer's** on the expenses side and on self-billed income (the customer issued it), otherwise the **counterpart's**. AFMs are compared after dropping spaces, dots, dashes and a Greek `EL`/`GR` prefix; other country prefixes are kept (`US987654321`), since foreign suppliers on 14.x documents carry them. With `--from/--to`, both sides are limited to that issue-date range.

Re-sent invoices (same `uid`, new MARK) count once: the highest MARK is kept and the older copies are listed under `superseded`, also for saved files created before this was added. An ERP row that still carries the old MARK is matched to the kept invoice and reported as `mismatched` with `ERP has superseded MARK ...`, so the ERP can be updated.

When an ERP row has no match but exactly one unmatched myDATA invoice has the same AFM and date, its `note` says `possible match: myDATA MARK ... but amount ...`; it usually means a typo or a fee/rounding difference.

## Output

```json
{
  "env": "test", "side": "expenses",
  "matched": [{"erpLine": 2, "mark": "...", "matchedOn": "mark", "afm": "...", "date": "...", "amount": "124.00", ...}],
  "mismatched": [{"...": "...", "differences": ["amount ERP 300.00 vs myDATA 310.00"]}],
  "missingInMyData": [{"erpLine": 5, "mark": "...", "afm": "...", "amount": "15.50", "note": ""}],
  "missingInErp": [...],
  "cancelledInMyData": [...],
  "superseded": [{"mark": "...", "supersededBy": "...", "uid": "..."}],
  "summary": {"matched": 2, "mismatched": 1, "missingInMyData": 1, "missingInErp": 0, "cancelledInMyData": 1, "erpRows": 4, "myDataInvoices": 4}
}
```

## What to tell the user

- Lead with the summary counts, then list `mismatched` and both `missing*` lists as short tables.
- `missingInMyData` on the expenses side usually means the supplier hasn't transmitted the invoice (or used another AFM); the business can ask the supplier to send it, or enter it itself as a 14.x / 13.x document through its own channel.
- `missingInErp` usually means an invoice was never booked; check whether it's a duplicate or belongs to another period before booking it.
- Don't fix anything automatically. Classification of matched-but-unclassified invoices goes through [classify.md](classify.md) with the normal confirmation step.
