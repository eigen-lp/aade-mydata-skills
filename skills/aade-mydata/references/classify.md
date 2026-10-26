# Expense classification (χαρακτηρισμός εξόδων)

**Sources** (cite these when explaining a choice):

- AADE, «Συνδυασμοί χαρακτηρισμών» spreadsheet v2.0.2, `syndiasmoi_xaraktirismwn_v2.0.2_0.xlsx`, on <https://www.aade.gr/en/mydata/technical-specifications-versions-mydata>. Its expense side is extracted, unchanged, into [`expense-combinations.json`](expense-combinations.json) by `tools/build_combinations.py`. **That file is the authority for which category and E3 code may go on which invoice type.**
- AADE, «Αναβαθμισμένη μέθοδος SendExpensesClassification» (postPerInvoice guidelines), <https://www.aade.gr/sites/default/files/2023-07/SendExpensesClassificationPostPerInvoiceGuidelines.pdf>.
- ERP technical description v2.0.2, §4.2.3 and code tables 8.2 (VAT categories), 8.10 (expense categories), 8.11 (expense types).

This is guidance for drafting proposals, not tax advice. The business's accountant decides the final classification.

## How to propose

1. Read the invoice in full (`docs --format json`): invoice type, lines (`netValue`, `vatCategory`, `vatAmount`, `vatExemptionCategory`), totals, issuer name and AFM, and any existing `expensesClassifications`.
2. Decide what the expense **is** for this business (goods for resale, raw materials, services, general expense, fixed asset...). Ask the user when the business purpose isn't obvious from the supplier and description; don't guess on assets vs. expenses or deductible vs. non-deductible VAT.
3. Pick the category (table A), then an E3 code allowed for that category **and** invoice type in `expense-combinations.json` (table B gives the meaning of common codes).
4. Add the VAT classification (table C), unless a rule below says not to.
5. Write the proposal JSON, run `classify --file proposal.json` (dry run), fix every `error:`, then show the table and ask for an explicit yes. Only then run the `send ... --confirm <code>` command that `classify` printed; `send` refuses if the proposal, `--mark` filter or environment changed since the dry run.

## Proposal file

Per-invoice mode (recommended; AADE's "upgraded" method). One E3 and one VAT entry for the whole invoice in the common case:

```json
{
  "postPerInvoice": true,
  "entityVatNumber": "only if an accountant acts for a client (9 digits)",
  "invoices": [
    {
      "mark": 400000000000101,
      "invType": "1.1",
      "note": "free text for the user, not sent",
      "classifications": [
        {"type": "E3_102_001", "category": "category2_1", "amount": "100.00"},
        {"type": "VAT_361", "amount": "100.00", "vatAmount": "24.00", "vatCategory": 1}
      ]
    }
  ]
}
```

Per-line mode (the original method): omit `postPerInvoice` and give `"lines": [{"lineNumber": 1, "classifications": [...]}]`, matching the invoice's line numbers.

Reject or flag an invoice instead of classifying it (only when the user explicitly asks): `{"mark": ..., "transactionMode": 1}` (1 = reject, disagreement) or `2` (deviation in amounts).

Always fill `invType`; it switches on the combination check. Amounts: non-negative, at most 2 decimals. `classificationMark` is assigned by AADE; don't include it.

## Rules for per-invoice mode (AADE guidelines)

- Send two kinds of entries for the whole invoice: E3 classifications and VAT classifications. `lineNumber` is ignored (the CLI sends 1).
- **E3 total** = net value + other taxes + stamp duty + fees of the invoice.
- With **category2_5** (general expenses *without* VAT deduction), the part of the invoice VAT not covered by VAT classifications is added to the E3 amount.
- With **category2_9** (expenses on behalf of third parties) there must be no other E3 entry and **no VAT entry**.
- Every `vatCategory`/`vatExemptionCategory` combination on the invoice must appear in the VAT entries, and vice versa; `amount` and `vatAmount` must equal the invoice's net and VAT totals for that combination.
- Lines with vatCategory 8 (no VAT, e.g. payroll) are not VAT-classified. Lines with vatCategory 7 (0%) are not either, except with vatExemptionCategory 16 (article 39a): then send `VAT_366`, vatCategory 1 (24%) or 4 (17%), vatExemptionCategory 16, amount = net of those lines, vatAmount > 0.
- Not for fuel invoices, and not for type 1.5 (use per-line mode for those).
- The same type/category combinations apply as in per-line mode.

## Recipient-entered documents (13.x, 14.x, 15.1, 16.1)

The business enters these itself, and AADE requires their expense classification **inside** the document when it is sent (outside this skill, e.g. by the ERP). They appear under `docs --include-self-entered`. `classify` can still re-classify them afterwards with SendExpensesClassification; the new classification replaces the original (verified on the test environment).

## A. Expense categories (table 8.10)

| Category | Meaning |
|---|---|
| category2_1 | Αγορές εμπορευμάτων: goods for resale |
| category2_2 | Αγορές α' και β' υλών: raw and auxiliary materials |
| category2_3 | Λήψη υπηρεσιών: services received |
| category2_4 | Γενικά έξοδα **με** δικαίωμα έκπτωσης ΦΠΑ: general expenses, VAT deductible |
| category2_5 | Γενικά έξοδα **χωρίς** δικαίωμα έκπτωσης ΦΠΑ: general expenses, VAT not deductible |
| category2_6 | Αμοιβές και παροχές προσωπικού: staff pay and benefits |
| category2_7 | Αγορές παγίων: fixed asset purchases |
| category2_8 | Αποσβέσεις παγίων: depreciation |
| category2_9 | Έξοδα για λογαριασμό τρίτων: expenses on behalf of third parties |
| category2_10 / 2_11 | Prior / next fiscal year expenses (any E3 code allowed above for that type) |
| category2_12 | Λοιπές εγγραφές τακτοποίησης εξόδων: other adjustment entries |
| category2_13 / 2_14 | Opening / closing inventory |
| category2_95 | Λοιπά πληροφοριακά στοιχεία εξόδων: informational, does not update E3 (send no type) |

## B. Common E3 expense codes (table 8.11)

| Code | Meaning |
|---|---|
| E3_102_001 / _002 | Purchases of goods for resale, wholesale / retail (net) |
| E3_102_004 / _005 | Purchases of goods, intra-EU / third countries |
| E3_202_001 / E3_302_001 | Raw materials purchases, wholesale (production / agricultural) |
| E3_581_001 / _002 / _003 | Employee benefits: gross pay / employer contributions / other |
| E3_585_001 | Management fees, domestic and foreign |
| E3_585_002 | Expenses from related companies |
| E3_585_005 | Reception and hospitality |
| E3_585_006 | Travel |
| E3_585_007 | Self-employed insurance contributions (ΕΦΚΑ) |
| E3_585_009 | Other fees for domestic services (e.g. accountant, lawyer, IT) |
| E3_585_010 | Other fees for foreign services |
| E3_585_011 / _012 / _013 | Energy / water / telecoms |
| E3_585_014 | Rent |
| E3_585_015 | Advertising and promotion |
| E3_585_016 | Other expenses |
| E3_586 | Interest and related charges |
| E3_587 | Depreciation |
| E3_588 | Unusual expenses, losses and fines |
| E3_882_001..004 | Tangible fixed assets: wholesale / retail / intra-EU / third countries |
| E3_883_001..004 | Intangible fixed assets, same split |

## C. VAT classification codes (table 8.11)

| Code | Meaning |
|---|---|
| VAT_361 | Domestic purchases and expenses |
| VAT_362 | Purchases and imports of investment goods (fixed assets) |
| VAT_363 | Other imports, except investment goods |
| VAT_364 | Intra-EU acquisitions of goods |
| VAT_365 | Intra-EU services received, art. 14.2.a |
| VAT_366 | Other recipient transactions (incl. art. 39a reverse charge) |
| NOT_VAT_295 | Not part of VAT (expenses / inputs, form Φ2) |

VAT entries take no `category`. vatCategory (table 8.2): 1 = 24%, 2 = 13%, 3 = 6%, 4 = 17%, 5 = 9%, 6 = 4%, 7 = 0%, 8 = no VAT, 9 = 3% and 10 = 4% (art. 31 ν.5057/2023).

## D. Allowed combinations for common invoice types

From AADE's spreadsheet (expense side). For any other type, look up `invoiceTypes["<type>"].expenses` in `expense-combinations.json`.

| Invoice type | Category → allowed E3 codes (abridged; the JSON is complete) |
|---|---|
| 1.1 sales invoice, 2.1 services invoice | 2_1: E3_102_001/003/006, E3_313_001/005 · 2_2: E3_202_001/005, E3_302_001/005 · 2_3: E3_585_001/002/004/005/006/008/009/011..016, E3_586, E3_581_003 · 2_4 and 2_5: the 2_3 codes plus goods, materials and (2_5 always, 2_4 on 1.1) E3_882_001/E3_883_001 · 2_7: E3_882_001, E3_883_001 (2.1 also _003/_004) |
| 14.1 / 14.2 intra-EU acquisitions | 2_1: E3_102_004/006, E3_313_003/005 · 2_4, 2_5: E3_585_002/003/016, E3_882_003, E3_883_003, E3_581_003 · 2_7: E3_882_003, E3_883_003 |
| 14.3 intra-EU services, 14.4 third-country services | 2_3: E3_585_001..006, E3_585_010, E3_585_015/016, E3_586, E3_581_003 · 2_7: E3_882_003/E3_883_003 (14.3), E3_882_004/E3_883_004 (14.4) |
| 14.5 ΕΦΚΑ and insurance funds | 2_5: E3_585_007, E3_588 |
| 16.1 rent (expense) | 2_5: E3_585_014, E3_585_016 |
| 17.1 payroll | 2_6: E3_581_001/002/003 |
| 17.2 depreciation | 2_8: E3_587 |
| 1.2, 1.3, 2.2, 2.3, 6.x, 8.4-8.6, 9.x, 10.x, 11.x, 17.3, 17.4 | no expense classifications listed |
| 1.6, 2.4, 5.1 | use the combinations of the correlated invoice's type |

All types also allow category2_10 / 2_11 (prior/next year, any code allowed above) and most allow category2_95 (informational, no E3 code).

## Typical first guesses (confirm with the user)

| Supplier / description | Category | E3 | VAT |
|---|---|---|---|
| Wholesaler, goods for resale | category2_1 | E3_102_001 | VAT_361 |
| Accountant, lawyer, IT contractor (Greek) | category2_3 | E3_585_009 | VAT_361 |
| Electricity / water / phone & internet | category2_4 | E3_585_011 / _012 / _013 | VAT_361 |
| Advertising (Greek supplier) | category2_3 or 2_4 | E3_585_015 | VAT_361 |
| Foreign SaaS billed under reverse charge (14.3 / 14.4) | category2_3 | E3_585_010 | VAT_365 (intra-EU) or VAT_366 |
| Laptop or equipment kept as an asset | category2_7 | E3_882_001 (Greek wholesale) | VAT_362 |
| Hotel, travel | category2_4 or 2_5 | E3_585_006 | VAT_361 if deductible |
| Business meals / hospitality | category2_5 (VAT usually not deductible) | E3_585_005 | ask the accountant; VAT not covered by a VAT entry is added to the E3 amount |

Deductibility of VAT and the asset/expense line depend on the business; these are starting points for the accountant, not rulings.
