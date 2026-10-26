# myDATA REST API notes (ERP channel)

Source: AADE, «Τεχνική περιγραφή διεπαφών REST API για διαβίβαση & λήψη δεδομένων από χρήστες ERP», **v2.0.2 official** (published September 2026) and the v2.0.2 XSD bundle, both on <https://www.aade.gr/en/mydata/technical-specifications-versions-mydata>. The test-environment page (<https://www.aade.gr/en/mydata-electronic-books/mydata/test-environment>) still links the v2.0.2 *preofficial* documents; v2.0.2 is now the official version on the specifications page.

## Endpoints this skill uses

| Command | Method | Endpoint | Spec § |
|---|---|---|---|
| `docs` | GET | `RequestDocs?mark=&dateFrom=&dateTo=&entityVatNumber=&invType=&maxMark=&nextPartitionKey=&nextRowKey=` | 4.2.6 |
| `transmitted` | GET | `RequestTransmittedDocs?` same parameters | 4.2.7 |
| `income` | GET | `RequestMyIncome?dateFrom=&dateTo=&counterVatNumber=&entityVatNumber=&invType=&nextPartitionKey=&nextRowKey=` | 4.2.8 |
| `expenses` | GET | `RequestMyExpenses?` same parameters | 4.2.9 |
| `classify --send` | POST | `SendExpensesClassification[?postPerInvoice=true]`, body `ExpensesClassificationsDoc` | 4.2.3 |

Not used in v1 (out of scope): `SendInvoices`, `CancelInvoice`, `SendIncomeClassification`, `SendPaymentsMethod`, `RequestVatInfo`, `RequestE3Info` and all delivery-note methods.

Headers on every call (§4.1.2): `aade-user-id`, `Ocp-Apim-Subscription-Key`.

## Parameters and paging

- `mark` (required for RequestDocs/RequestTransmittedDocs): returns entries with MARK **greater than** this. `docs` sends `0` unless you pass `--mark`.
- Dates go out as `dd/MM/yyyy` (§4.2.8 note 2). The CLI accepts `YYYY-MM-DD` or `DD/MM/YYYY`. If only one of dateFrom/dateTo is given, AADE searches that single day.
- If a response is too large, it carries `<continuationToken><nextPartitionKey/><nextRowKey/></continuationToken>`; the CLI repeats the call with those two parameters until no token comes back (cap: 500 pages).
- For RequestDocs the spec's parameter table says `receiverVatNumber` while its URL template says `counterVatNumber`. To avoid guessing, `docs --counterpart` filters locally on issuer/counterpart AFM instead of sending either.

## Responses

- **RequestDocs / RequestTransmittedDocs** → `RequestedDoc` (§6.2, `requestedInvoicesDoc-v2.0.2.xsd`): `invoicesDoc/invoice` (AadeBookInvoiceType: `mark`, `uid`, `cancelledByMark`, `issuer`, `counterpart`, `invoiceHeader`, `invoiceDetails`, `invoiceSummary`...), `cancelledInvoicesDoc`, `incomeClassificationsDoc`, `expensesClassificationsDoc`, `paymentMethodsDoc`. RequestDocs also tells you when a counterparty rejected one of your invoices (`transactionMode` in `expensesClassificationsDoc`).
- **RequestMyIncome / RequestMyExpenses** → `RequestedBookInfo` (§6.3): rows of `bookInfo` grouped by counterpart AFM + issue date + invoice type: `netValue`, `vatAmount`, `withheldAmount`, `otherTaxesAmount`, `stampDutyAmount`, `feesAmount`, `deductionsAmount`, `thirdPartyAmount`, `grossValue`, `count`, `minMark`, `maxMark`. These are **totals, not individual invoices**; use `docs` when you need MARKs.
- **SendExpensesClassification** → `ResponseDoc` (§6.1): one `response` per submitted invoice with `statusCode` (`Success`, `ValidationError`, `TechnicalError`, `XMLSyntaxError`), `invoiceMark`, `classificationMark` on success, or `errors/error/{message,code}`.

The CLI parses responses without relying on XML namespaces: AADE's own sample `RequestUpdatedExpClassResponse.xml` puts `expensesInvoiceClassification` in the `ecls:` namespace while the XSD expects the invoice namespace.

## Behaviour worth knowing

- Re-sending a classification for the same invoice replaces the previous one; AADE keeps the latest as valid (§4.1.1).
- `classificationMark` is assigned by AADE; never send it.
- `classificationPostMode` (0 old way / 1 per invoice) appears in responses; it is set by AADE, not sent.
- Business error codes are listed in §7.2 of the spec.

## v2.0.2 vs v2.0.1 / v2.0.0

The v2.0.x changes are about digital delivery notes (new methods `RegisterTransfer`, `ConfirmDeliveryOutcome`, `RejectDeliveryNote`, `GetDeliveryNoteStatus`, `GenerateGroupQRCode`, `RequestGroupQRDetails`, `ConfirmDeliveryReturn`; new header fields such as `receivingNotePurpose`, `nonObligatedRecipient`, `withoutDigitalTransportTracking`; document types 1.4, 3.1, 3.2, 11.5 can now act as delivery notes). Invoices you read may carry the new delivery fields (`invoiceDeliveryStatus`, `deliveryLifecycle`, `packingsDeclarations`); the CLI passes them through untouched. The read endpoints and `SendExpensesClassification` used here are unchanged; `ExpensesClassificationType` still has `classificationType`, `classificationCategory`, `amount`, `vatAmount`, `vatCategory` (1-10), `vatExemptionCategory` (1-31) and `id`.
