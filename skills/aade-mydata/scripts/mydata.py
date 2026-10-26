#!/usr/bin/env python3
"""mydata.py - read AADE myDATA books, classify expenses, reconcile with an ERP.

Python 3.9+ standard library only. Talks to the myDATA REST API (ERP channel),
technical spec v2.0.2 (official). Not affiliated with AADE. Not tax advice.

Credentials come from environment variables and are never printed:
    MYDATA_USER_ID            -> header aade-user-id
    MYDATA_SUBSCRIPTION_KEY   -> header Ocp-Apim-Subscription-Key
    MYDATA_ENV                -> "test" (default) or "prod"

Run `python3 mydata.py --help` for the subcommands.
"""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

VERSION = "0.2.0"
SPEC_VERSION = "2.0.2"

BASE_URLS = {
    # Development/test environment, from AADE's "test URLs" list.
    "test": "https://mydataapidev.aade.gr/",
    # Production, from the official ERP technical description v2.0.2, section 4.2.
    "prod": "https://mydatapi.aade.gr/myDATA/",
}

ECLS_NS = "https://www.aade.gr/myDATA/expensesClassificaton/v1.0"  # sic, AADE's spelling
MS_SERIALIZATION_NS = "http://schemas.microsoft.com/2003/10/Serialization/"
COMBINATIONS_FILE = Path(__file__).resolve().parent.parent / "references" / "expense-combinations.json"

EXPENSE_CATEGORIES = {f"category2_{n}" for n in (*range(1, 15), 95)}
VAT_CLASSIFICATION_PREFIXES = ("VAT_", "NOT_VAT_")

# Elements that can repeat; always emitted as JSON lists so output shape is stable.
LIST_TAGS = {
    "invoice", "cancelledInvoice", "incomeInvoiceClassification", "expensesInvoiceClassification",
    "paymentMethods", "paymentMethodDetails", "invoiceDetails", "taxes", "incomeClassification",
    "expensesClassification", "invoicesIncomeClassificationDetails",
    "invoicesExpensesClassificationDetails", "incomeClassificationDetailData",
    "expensesClassificationDetailData", "correlatedInvoices", "multipleConnectedMarks",
    "bookInfo", "response", "error", "otherCorrelatedEntities",
}

BOOK_FIELDS = [
    "issueDate", "invType", "counterVatNumber", "selfpricing", "invoiceDetailType", "netValue",
    "vatAmount", "withheldAmount", "otherTaxesAmount", "stampDutyAmount", "feesAmount",
    "deductionsAmount", "thirdPartyAmount", "grossValue", "count", "minMark", "maxMark",
]

SUMMARY_FIELDS = [
    "mark", "issueDate", "invoiceType", "series", "aa", "issuerVat", "issuerName",
    "counterpartVat", "counterpartName", "netValue", "vatAmount", "grossValue",
    "cancelledByMark", "selfPricing", "classified",
]

# Β1/Β2 "recipient-entered" documents (spec §5, footnote 1): the business transmits them itself,
# so they appear in RequestTransmittedDocs even though they are its expenses.
SELF_ENTERED_TYPES = {"13.1", "13.2", "13.3", "13.4", "13.30", "13.31", "14.1", "14.2", "14.3",
                      "14.4", "14.5", "14.30", "14.31", "15.1", "16.1"}
# Delivery notes (table 8.1): movement documents with no value; AADE's books ignore them.
NON_FINANCIAL_TYPES = {"9.1", "9.2", "9.3", "10.1", "10.2"}

MAX_PAGES = 500
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 120  # seconds
_sleep = time.sleep  # replaced in tests


class MyDataError(Exception):
    """An error with a message that is safe to show (never contains credentials)."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- config


ENV_FILE_KEYS = {"MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY", "MYDATA_ENV"}


def load_env_file(path):
    """Read MYDATA_* settings from a dotenv-style file (KEY=VALUE lines) into the environment."""
    try:
        lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise MyDataError(f"Cannot read --env-file {path}: {e.strerror}", 2)
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in ENV_FILE_KEYS:
            os.environ[key] = value


def get_env(args_env=None):
    env = (args_env or os.environ.get("MYDATA_ENV") or "test").strip().lower()
    if env not in BASE_URLS:
        raise MyDataError(f"Unknown environment '{env}'. Use 'test' or 'prod'.", 2)
    return env


def get_credentials():
    user = os.environ.get("MYDATA_USER_ID", "").strip()
    key = os.environ.get("MYDATA_SUBSCRIPTION_KEY", "").strip()
    missing = [n for n, v in (("MYDATA_USER_ID", user), ("MYDATA_SUBSCRIPTION_KEY", key)) if not v]
    if missing:
        raise MyDataError(
            "Missing environment variable(s): " + ", ".join(missing) + ".\n"
            "Set them in your shell (not in the chat), e.g. in ~/.zshrc or a .env file you source.\n"
            "Test-environment credentials: https://mydata-dev-register.azurewebsites.net",
            3,
        )
    return user, key


def redact(text):
    """Remove credential values from any text before it is shown."""
    for name in ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY"):
        value = os.environ.get(name, "").strip()
        if len(value) >= 3:
            text = text.replace(value, f"<{name}>")
    return text


# --------------------------------------------------------------------------- HTTP


def _transport(method, url, headers, body=None, timeout=60):
    """Perform one HTTP request. Tests replace this function; nothing else does I/O."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise MyDataError(f"Network error reaching {urllib.parse.urlsplit(url).netloc}: {e.reason}")


def call(env, method, endpoint, params=None, body=None):
    user, key = get_credentials()
    url = BASE_URLS[env] + endpoint
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers = {
        "aade-user-id": user,
        "Ocp-Apim-Subscription-Key": key,
        "Accept": "application/xml",
        "User-Agent": f"aade-mydata-skills/{VERSION}",
    }
    if body is not None:
        headers["Content-Type"] = "application/xml"
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        status, data = _transport(method, url, headers, body)
        if status != 429 or attempt == RATE_LIMIT_RETRIES:
            break
        # AADE answers 429 with e.g. "Rate limit is exceeded. Try again in 65 seconds."
        m = re.search(rb"(\d+)\s*seconds", data or b"")
        wait = min(int(m.group(1)) if m else 30, RATE_LIMIT_MAX_WAIT) + 1
        print(f"myDATA rate limit reached; waiting {wait}s before retrying {endpoint}...", file=sys.stderr)
        _sleep(wait)
    if status == 429:
        raise MyDataError(f"HTTP 429 from myDATA {endpoint}: rate limit still exceeded after "
                          f"{RATE_LIMIT_RETRIES} retries. Try again later or narrow the date range.", 5)
    if status == 401:
        raise MyDataError(
            f"HTTP 401 from myDATA ({env}): credentials rejected. Check MYDATA_USER_ID and "
            f"MYDATA_SUBSCRIPTION_KEY, and that they belong to the '{env}' environment "
            "(test and production use different registrations).", 4)
    if status == 403:
        raise MyDataError(f"HTTP 403 from myDATA ({env}): access denied for this user or entity.", 4)
    if status >= 400:
        snippet = redact(data[:500].decode("utf-8", "replace"))
        raise MyDataError(f"HTTP {status} from myDATA {endpoint}: {snippet}", 5)
    return data


# --------------------------------------------------------------------------- XML helpers


def parse_xml(data):
    """Parse untrusted XML. DTDs are refused (blocks entity expansion and XXE)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise MyDataError("Refusing XML that contains a DOCTYPE/ENTITY declaration.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise MyDataError(f"myDATA returned XML that could not be parsed: {e}")
    # The API can wrap the real document, escaped, in a .NET <string> envelope
    # (seen on mydataapidev.aade.gr); unwrap it and parse the inner XML.
    if root.tag == f"{{{MS_SERIALIZATION_NS}}}string" and (root.text or "").lstrip().startswith("<"):
        return parse_xml(root.text.strip())
    return root


def local(tag):
    return tag.rsplit("}", 1)[-1]


def to_obj(elem):
    """Convert an element into plain dicts/lists/strings, dropping namespaces."""
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    out = {}
    for child in children:
        name = local(child.tag)
        value = to_obj(child)
        if name in LIST_TAGS:
            out.setdefault(name, []).append(value)
        elif name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(value)
        else:
            out[name] = value
    return out


def continuation(root):
    for el in root.iter():
        if local(el.tag) == "continuationToken":
            vals = {local(c.tag): (c.text or "").strip() for c in el}
            if vals.get("nextPartitionKey") or vals.get("nextRowKey"):
                return vals.get("nextPartitionKey", ""), vals.get("nextRowKey", "")
    return None


def fetch_pages(env, endpoint, params):
    """GET an endpoint, following nextPartitionKey/nextRowKey until exhausted."""
    params = dict(params)
    seen = set()
    for _ in range(MAX_PAGES):
        root = parse_xml(call(env, "GET", endpoint, params))
        yield root
        token = continuation(root)
        if not token or token in seen:
            return
        seen.add(token)
        params["nextPartitionKey"], params["nextRowKey"] = token
    raise MyDataError(f"Stopped after {MAX_PAGES} pages from {endpoint}; narrow the date range.")


# --------------------------------------------------------------------------- dates & numbers


def parse_date(text):
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise MyDataError(f"Unrecognised date '{text}'. Use YYYY-MM-DD or DD/MM/YYYY.", 2)


def api_date(d):
    return d.strftime("%d/%m/%Y")  # spec 4.2.8 note 2: dd/MM/yyyy


def parse_amount(text):
    """Parse 1234.56, 1.234,56 or 1,234.56 into Decimal."""
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text))
    s = str(text or "").strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise MyDataError(f"Unrecognised amount '{text}'.", 2)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- read commands


def request_docs(env, endpoint, args):
    params = {
        "mark": str(args.mark),
        "dateFrom": api_date(parse_date(args.date_from)) if args.date_from else None,
        "dateTo": api_date(parse_date(args.date_to)) if args.date_to else None,
        "entityVatNumber": args.entity,
        "invType": args.inv_type,
        "maxMark": args.max_mark,
    }
    result = {"invoices": [], "cancelledInvoices": [], "incomeClassifications": [],
              "expensesClassifications": [], "paymentMethods": []}
    keys = {"invoicesDoc": ("invoices", "invoice"),
            "cancelledInvoicesDoc": ("cancelledInvoices", "cancelledInvoice"),
            "incomeClassificationsDoc": ("incomeClassifications", "incomeInvoiceClassification"),
            "expensesClassificationsDoc": ("expensesClassifications", "expensesInvoiceClassification"),
            "paymentMethodsDoc": ("paymentMethods", "paymentMethods")}
    pages = 0
    for root in fetch_pages(env, endpoint, params):
        pages += 1
        merge_requested_doc(root, result, keys)
    result["invoices"], result["superseded"] = drop_superseded(result["invoices"])
    if args.counterpart:
        result["invoices"] = [i for i in result["invoices"] if args.counterpart in (
            party_vat(i, "issuer"), party_vat(i, "counterpart"))]
    return {"env": env, "method": endpoint, "specVersion": SPEC_VERSION, "pages": pages,
            "params": {k: v for k, v in params.items() if v}, **result}


def drop_superseded(invoices):
    """Keep one transmission per invoice uid.

    A supplier that re-sends an invoice with the same identifying fields (same uid) gets a new
    MARK; AADE keeps the latest as valid and voids the earlier one (spec §4.1.1), but
    RequestDocs still returns both, without cancelledByMark. Keep the highest MARK per uid.
    Explicitly cancelled copies (cancelledByMark) are left alone; they are reported as cancelled.
    Returns (kept, superseded) where superseded lists {mark, supersededBy, uid}.
    """
    latest = {}
    for inv in invoices:
        uid = inv.get("uid")
        if uid and not inv.get("cancelledByMark") and (
                uid not in latest or _mark_int(inv) > _mark_int(latest[uid])):
            latest[uid] = inv
    kept, superseded = [], []
    for inv in invoices:
        uid = inv.get("uid")
        if uid in latest and latest[uid] is not inv and not inv.get("cancelledByMark"):
            if inv.get("mark") != latest[uid].get("mark"):
                superseded.append({"mark": inv.get("mark", ""), "supersededBy": latest[uid].get("mark", ""),
                                   "uid": uid})
            continue
        kept.append(inv)
    return kept, superseded


def _mark_int(inv):
    try:
        return int(inv.get("mark") or 0)
    except ValueError:
        return 0


def merge_requested_doc(root, result, keys):
    obj = to_obj(root)
    if not isinstance(obj, dict):
        return
    for section, (target, item) in keys.items():
        block = obj.get(section)
        if isinstance(block, dict):
            result[target].extend(block.get(item, []))


def party_vat(inv, role):
    party = inv.get(role)
    return party.get("vatNumber", "") if isinstance(party, dict) else ""


def party_name(inv, role):
    party = inv.get(role)
    return party.get("name", "") if isinstance(party, dict) else ""


def summarize_invoice(inv):
    head = inv.get("invoiceHeader") or {}
    summ = inv.get("invoiceSummary") or {}
    classified = bool(summ.get("expensesClassification")) or any(
        isinstance(d, dict) and d.get("expensesClassification") for d in inv.get("invoiceDetails", []))
    return {
        "mark": inv.get("mark", ""),
        "issueDate": head.get("issueDate", ""),
        "invoiceType": head.get("invoiceType", ""),
        "series": head.get("series", ""),
        "aa": head.get("aa", ""),
        "issuerVat": party_vat(inv, "issuer"),
        "issuerName": party_name(inv, "issuer"),
        "counterpartVat": party_vat(inv, "counterpart"),
        "counterpartName": party_name(inv, "counterpart"),
        "netValue": summ.get("totalNetValue", ""),
        "vatAmount": summ.get("totalVatAmount", ""),
        "grossValue": summ.get("totalGrossValue", ""),
        "cancelledByMark": inv.get("cancelledByMark", ""),
        "selfPricing": "yes" if is_self_billed(inv) else "",
        "classified": "yes" if classified else "no",
    }


def request_book(env, endpoint, args):
    params = {
        "dateFrom": api_date(parse_date(args.date_from)),
        "dateTo": api_date(parse_date(args.date_to)),
        "counterVatNumber": args.counterpart,
        "entityVatNumber": args.entity,
        "invType": args.inv_type,
    }
    rows, pages = [], 0
    for root in fetch_pages(env, endpoint, params):
        pages += 1
        obj = to_obj(root)
        if isinstance(obj, dict):
            rows.extend(r for r in obj.get("bookInfo", []) if isinstance(r, dict))
    return {"env": env, "method": endpoint, "specVersion": SPEC_VERSION, "pages": pages,
            "params": {k: v for k, v in params.items() if v}, "rows": rows}


def csv_safe(value):
    """Neutralise spreadsheet formulas in third-party text (e.g. issuer names)."""
    if isinstance(value, (dict, list)):
        return ""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return "'" + text
    return text


def write_csv(rows, fields, out):
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: csv_safe(r.get(k, "")) for k in fields})


def emit_json(obj, out=None):
    (out or sys.stdout).write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def cmd_check(args):
    env = get_env(args.env)
    print(f"environment: {env}")
    print(f"base URL:    {BASE_URLS[env]}")
    print(f"spec:        myDATA REST API v{SPEC_VERSION} (ERP channel)")
    for name in ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY"):
        print(f"{name}: {'set' if os.environ.get(name, '').strip() else 'NOT SET'}")
    today = api_date(date.today())
    call(env, "GET", "RequestMyIncome", {"dateFrom": today, "dateTo": today})
    print("credentials: OK (RequestMyIncome for today answered)")
    return 0


def is_self_entered(inv):
    return (inv.get("invoiceHeader") or {}).get("invoiceType", "") in SELF_ENTERED_TYPES


def is_self_billed(inv):
    return str((inv.get("invoiceHeader") or {}).get("selfPricing", "")).lower() == "true"


def book_side(inv, source):
    """Which of the business's books a document belongs to: "expenses", "income" or None.

    Matches AADE's own RequestMyExpenses/RequestMyIncome grouping (checked on production data):
    - delivery notes (9.x, 10.x) are in neither book;
    - RequestDocs: self-billed (selfPricing=true) documents are the recipient's income,
      everything else its expenses;
    - RequestTransmittedDocs: recipient-entered types (13.x/14.x/15.1/16.1) are expenses,
      everything else income.
    """
    if (inv.get("invoiceHeader") or {}).get("invoiceType", "") in NON_FINANCIAL_TYPES:
        return None
    if source == "RequestDocs":
        return "income" if is_self_billed(inv) else "expenses"
    if source == "RequestTransmittedDocs":
        return "expenses" if is_self_entered(inv) else "income"
    return None


def book_invoices(env, args, side):
    """All documents in one book (side), fetched from both RequestDocs and RequestTransmittedDocs."""
    received = request_docs(env, "RequestDocs", args)
    own = request_docs(env, "RequestTransmittedDocs", args)
    excluded = {"deliveryNotes": 0, "otherBook": 0}
    keep, seen = [], set()
    for source, docs in (("RequestDocs", received), ("RequestTransmittedDocs", own)):
        for inv in docs["invoices"]:
            where = book_side(inv, source)
            if where == side and inv.get("mark") not in seen:
                seen.add(inv.get("mark"))
                keep.append(inv)
            elif where is None:
                excluded["deliveryNotes"] += 1
            elif where != side and source == "RequestDocs":
                excluded["otherBook"] += 1
    received["invoices"] = keep
    received["expensesClassifications"].extend(own["expensesClassifications"])
    received["incomeClassifications"].extend(own["incomeClassifications"])
    received["pages"] += own["pages"]
    received["superseded"].extend(own["superseded"])
    received["method"] = f"RequestDocs+RequestTransmittedDocs ({side})"
    received["excluded"] = excluded
    return received


def expense_invoices(env, args):
    """The business's expenses: received documents (minus self-billed income and delivery
    notes) plus its own recipient-entered 13.x/14.x/15.1/16.1 documents."""
    return book_invoices(env, args, "expenses")


def cmd_docs(args, endpoint):
    env = get_env(args.env)
    if endpoint == "RequestDocs" and getattr(args, "include_self_entered", False):
        result = expense_invoices(env, args)
    else:
        result = request_docs(env, endpoint, args)
    # Classifications sent separately (SendExpensesClassification) arrive in their own list.
    sent = {c.get("invoiceMark") for c in result["expensesClassifications"] if isinstance(c, dict)}
    rows = [summarize_invoice(i) for i in result["invoices"]]
    for r in rows:
        if r["mark"] in sent:
            r["classified"] = "yes"
    if args.format == "csv":
        write_csv(rows, SUMMARY_FIELDS, sys.stdout)
    elif args.format == "summary":
        emit_json({k: v for k, v in result.items() if k != "invoices"} | {"invoices": rows})
    else:
        emit_json(result)
    return 0


def cmd_book(args, endpoint):
    result = request_book(get_env(args.env), endpoint, args)
    if args.format == "csv":
        write_csv(result["rows"], BOOK_FIELDS, sys.stdout)
    else:
        emit_json(result)
    return 0


# --------------------------------------------------------------------------- classification


def load_combinations():
    try:
        return json.loads(COMBINATIONS_FILE.read_text(encoding="utf-8"))["invoiceTypes"]
    except (OSError, ValueError, KeyError):
        return None


def is_vat_classification(ctype):
    return bool(ctype) and ctype.startswith(VAT_CLASSIFICATION_PREFIXES)


def normalise_proposal(proposal):
    """Validate a proposal and return (invoices, errors, warnings).

    Each returned invoice: {mark, entityVatNumber, invType, transactionMode | lines}
    where lines = [{lineNumber, classifications:[...]}].
    """
    errors, warnings = [], []
    if not isinstance(proposal, dict) or not isinstance(proposal.get("invoices"), list) or not proposal["invoices"]:
        return [], ["Proposal must be a JSON object with a non-empty 'invoices' list."], []
    per_invoice = bool(proposal.get("postPerInvoice", False))
    combos = load_combinations()
    if combos is None:
        warnings.append("expense-combinations.json not found; invoice-type checks skipped.")
    out = []
    for n, raw in enumerate(proposal["invoices"], 1):
        where = f"invoice #{n}"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be an object.")
            continue
        try:
            mark = int(str(raw.get("mark", "")).strip())
            assert mark > 0
        except (ValueError, AssertionError):
            errors.append(f"{where}: 'mark' must be a positive integer (the invoice MARK).")
            continue
        where = f"MARK {mark}"
        inv = {"mark": mark, "entityVatNumber": str(raw.get("entityVatNumber") or proposal.get("entityVatNumber") or ""),
               "invType": str(raw.get("invType") or ""), "lines": []}
        if inv["entityVatNumber"] and not re.fullmatch(r"\d{9}", inv["entityVatNumber"]):
            errors.append(f"{where}: entityVatNumber must be a 9-digit Greek AFM.")
        modes = [k for k in ("transactionMode", "lines", "classifications") if raw.get(k) not in (None, [], "")]
        if len(modes) != 1:
            errors.append(f"{where}: give exactly one of 'transactionMode', 'lines' or 'classifications'.")
            continue
        if modes[0] == "transactionMode":
            if raw["transactionMode"] not in (1, 2, "1", "2"):
                errors.append(f"{where}: transactionMode must be 1 (reject) or 2 (deviation).")
                continue
            inv["transactionMode"] = int(raw["transactionMode"])
            out.append(inv)
            continue
        if modes[0] == "classifications":
            if not per_invoice:
                errors.append(f"{where}: invoice-level 'classifications' need \"postPerInvoice\": true; "
                              "otherwise give per-line 'lines'.")
                continue
            raw_lines = [{"lineNumber": raw.get("lineNumber", 1), "classifications": raw["classifications"]}]
        else:
            raw_lines = raw["lines"]
        for ln in raw_lines:
            try:
                line_no = int(ln.get("lineNumber"))
            except (TypeError, ValueError, AttributeError):
                errors.append(f"{where}: every line needs an integer lineNumber.")
                continue
            items = []
            for c in ln.get("classifications") or []:
                item, errs = normalise_classification(c, f"{where} line {line_no}", per_invoice)
                errors.extend(errs)
                if item:
                    items.append(item)
            if not items:
                errors.append(f"{where} line {line_no}: no classifications.")
            inv["lines"].append({"lineNumber": line_no, "classifications": items})
        check_invoice_rules(inv, per_invoice, combos, errors, warnings)
        out.append(inv)
    return out, errors, warnings


def normalise_classification(c, where, per_invoice):
    errs = []
    if not isinstance(c, dict):
        return None, [f"{where}: classification must be an object."]
    ctype = str(c.get("type") or c.get("classificationType") or "").strip()
    cat = str(c.get("category") or c.get("classificationCategory") or "").strip()
    if ctype and not re.fullmatch(r"(E3|VAT|NOT_VAT)_[0-9_]+", ctype):
        errs.append(f"{where}: unknown classificationType '{ctype}'.")
    if cat and cat not in EXPENSE_CATEGORIES:
        errs.append(f"{where}: unknown expense category '{cat}' (expected category2_1..14 or category2_95).")
    if not ctype and not cat:
        errs.append(f"{where}: needs a 'type' and/or 'category'.")
    item = {"type": ctype, "category": cat}
    for field in ("amount", "vatAmount"):
        if c.get(field) in (None, ""):
            continue
        try:
            value = parse_amount(c[field])
        except MyDataError:
            errs.append(f"{where}: {field} '{c[field]}' is not a number.")
            continue
        if value < 0:
            errs.append(f"{where}: {field} must be >= 0 (AmountType has minInclusive 0).")
        if value != value.quantize(Decimal("0.01")):
            errs.append(f"{where}: {field} {value} has more than 2 decimals.")
        item[field] = money(value)
    if "amount" not in item:
        errs.append(f"{where}: 'amount' is required.")
    for field, lo, hi in (("vatCategory", 1, 10), ("vatExemptionCategory", 1, 31)):
        if c.get(field) in (None, ""):
            continue
        try:
            value = int(c[field])
            assert lo <= value <= hi
            item[field] = value
        except (TypeError, ValueError, AssertionError):
            errs.append(f"{where}: {field} must be an integer {lo}-{hi}.")
    if is_vat_classification(ctype):
        if cat:
            errs.append(f"{where}: VAT classification {ctype} takes no category.")
        if per_invoice and ("vatCategory" not in item or "vatAmount" not in item):
            errs.append(f"{where}: {ctype} needs vatCategory and vatAmount when postPerInvoice is true.")
    elif ctype and not cat:
        errs.append(f"{where}: E3 classification {ctype} needs a category.")
    return (item if not errs else None), errs


def check_invoice_rules(inv, per_invoice, combos, errors, warnings):
    where = f"MARK {inv['mark']}"
    items = [c for ln in inv["lines"] for c in ln["classifications"]]
    e3 = [c for c in items if not is_vat_classification(c["type"])]
    vat = [c for c in items if is_vat_classification(c["type"])]
    if any(c["category"] == "category2_9" for c in e3):
        if len(e3) > 1 or (per_invoice and vat):
            errors.append(f"{where}: category2_9 (expenses on behalf of third parties) must be the only "
                          "E3 classification, with no VAT classification (AADE postPerInvoice guidelines, rule 7.iii).")
    inv_type = inv["invType"]
    if not inv_type:
        warnings.append(f"{where}: no invType given; invoice-type combination check skipped.")
        return
    if combos is None:
        return
    spec = combos.get(inv_type)
    if spec is None:
        errors.append(f"{where}: unknown invoice type '{inv_type}'.")
        return
    allowed = spec.get("expenses")
    if allowed is None:
        errors.append(f"{where}: AADE's combinations table lists no expense classifications for type {inv_type}.")
        return
    if allowed == "correlated":
        warnings.append(f"{where}: type {inv_type} uses the combinations of its correlated invoice; check those.")
        return
    any_above = sorted({t for v in allowed.values() if isinstance(v, list) for t in v})
    for c in e3:
        cat, ctype = c["category"], c["type"]
        if cat not in allowed:
            errors.append(f"{where}: category {cat} is not allowed for invoice type {inv_type}.")
            continue
        rule = allowed[cat]
        if rule == "no-e3":
            if ctype:
                errors.append(f"{where}: {cat} on type {inv_type} does not update E3; omit classificationType ({ctype}).")
        elif rule == "any-above":
            if ctype and ctype not in any_above:
                errors.append(f"{where}: {ctype} is not allowed for type {inv_type}.")
        elif not ctype:
            errors.append(f"{where}: {cat} on type {inv_type} needs a classificationType.")
        elif ctype not in rule:
            errors.append(f"{where}: {ctype} is not allowed with {cat} on invoice type {inv_type} "
                          f"(allowed: {', '.join(rule)}).")


def build_classification_xml(invoices):
    ET.register_namespace("", ECLS_NS)
    q = lambda name: f"{{{ECLS_NS}}}{name}"  # noqa: E731
    root = ET.Element(q("ExpensesClassificationsDoc"))
    for inv in invoices:
        node = ET.SubElement(root, q("expensesInvoiceClassification"))
        ET.SubElement(node, q("invoiceMark")).text = str(inv["mark"])
        if inv["entityVatNumber"]:
            ET.SubElement(node, q("entityVatNumber")).text = inv["entityVatNumber"]
        if "transactionMode" in inv:
            ET.SubElement(node, q("transactionMode")).text = str(inv["transactionMode"])
            continue
        for ln in inv["lines"]:
            detail = ET.SubElement(node, q("invoicesExpensesClassificationDetails"))
            ET.SubElement(detail, q("lineNumber")).text = str(ln["lineNumber"])
            for c in ln["classifications"]:
                data = ET.SubElement(detail, q("expensesClassificationDetailData"))
                # Element order follows ExpensesClassificationType in the XSD.
                if c["type"]:
                    ET.SubElement(data, q("classificationType")).text = c["type"]
                if c["category"]:
                    ET.SubElement(data, q("classificationCategory")).text = c["category"]
                ET.SubElement(data, q("amount")).text = str(c["amount"])
                if "vatAmount" in c:
                    ET.SubElement(data, q("vatAmount")).text = str(c["vatAmount"])
                if "vatCategory" in c:
                    ET.SubElement(data, q("vatCategory")).text = str(c["vatCategory"])
                if "vatExemptionCategory" in c:
                    ET.SubElement(data, q("vatExemptionCategory")).text = str(c["vatExemptionCategory"])
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"


def plan_table(invoices, env, per_invoice):
    rows = [("MARK", "invType", "line", "classificationType", "category", "amount", "vatAmount", "vatCat", "vatExempt")]
    for inv in invoices:
        if "transactionMode" in inv:
            label = {1: "REJECT invoice (transactionMode=1)", 2: "DEVIATION (transactionMode=2)"}[inv["transactionMode"]]
            rows.append((str(inv["mark"]), inv["invType"], "-", label, "", "", "", "", ""))
            continue
        for ln in inv["lines"]:
            for c in ln["classifications"]:
                rows.append((str(inv["mark"]), inv["invType"], str(ln["lineNumber"]), c["type"] or "-",
                             c["category"] or "-", str(c["amount"]), str(c.get("vatAmount", "")),
                             str(c.get("vatCategory", "")), str(c.get("vatExemptionCategory", ""))))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = [f"Target: SendExpensesClassification on {env.upper()} ({BASE_URLS[env]})"
             f"{'  postPerInvoice=true' if per_invoice else ''}"]
    for i, r in enumerate(rows):
        lines.append("| " + " | ".join(v.ljust(widths[j]) for j, v in enumerate(r)) + " |")
        if i == 0:
            lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return "\n".join(lines)


def parse_response_doc(data):
    obj = to_obj(parse_xml(data))
    results = []
    for r in obj.get("response", []) if isinstance(obj, dict) else []:
        errs = r.get("errors", {}) if isinstance(r, dict) else {}
        results.append({
            "index": r.get("index", ""),
            "statusCode": r.get("statusCode", ""),
            "invoiceMark": r.get("invoiceMark", ""),
            "classificationMark": r.get("classificationMark", ""),
            "errors": [{"code": e.get("code", ""), "message": e.get("message", "")}
                       for e in (errs.get("error", []) if isinstance(errs, dict) else [])],
        })
    return results


def prepare_classification(args, env):
    """Load, filter and validate a proposal; return (invoices, per_invoice, xml, code)."""
    try:
        proposal = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise MyDataError(f"Cannot read proposal {args.file}: {e}", 2)
    if args.mark:
        proposal["invoices"] = [i for i in proposal.get("invoices", [])
                                if isinstance(i, dict) and str(i.get("mark")) in {str(m) for m in args.mark}]
        if not proposal["invoices"]:
            raise MyDataError("None of the --mark values are in the proposal file.", 2)
    invoices, errors, warnings = normalise_proposal(proposal)
    per_invoice = bool(proposal.get("postPerInvoice", False))
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        raise MyDataError(f"{len(errors)} problem(s) in the proposal; nothing was sent.", 6)
    xml = build_classification_xml(invoices)
    return invoices, per_invoice, xml, confirmation_code(env, per_invoice, xml)


def confirmation_code(env, per_invoice, xml):
    """Short fingerprint of exactly what `send` would transmit, and where."""
    digest = hashlib.sha256(f"{env}\n{per_invoice}\n".encode() + xml).hexdigest()
    return digest[:10]


def cmd_classify(args):
    """Dry run only: validate, print the table, the XML and the confirmation code."""
    env = get_env(args.env)
    invoices, per_invoice, xml, code = prepare_classification(args, env)
    if args.xml_out:
        Path(args.xml_out).write_bytes(xml)
    if args.xml_only:
        sys.stdout.write(xml.decode("utf-8"))
        return 0
    marks = "".join(f" --mark {m}" for m in args.mark or [])
    prod = " --yes-production" if env == "prod" else ""
    print("DRY RUN - nothing sent. Review this with the user (and their accountant):\n")
    print(plan_table(invoices, env, per_invoice))
    print("\nXML body that `send` would POST:\n")
    sys.stdout.write(xml.decode("utf-8"))
    print(f"\nConfirmation code: {code}")
    print("Only after the user explicitly approves this exact table, run:")
    print(f"  mydata.py send --env {env} --file {args.file}{marks} --confirm {code}{prod}")
    return 0


def cmd_send(args):
    """The only write: SendExpensesClassification, for a proposal confirmed by code."""
    env = get_env(args.env)
    if env == "prod" and not args.yes_production:
        raise MyDataError(
            "Refusing to send to PRODUCTION without --yes-production.\n"
            "Production classifications update the taxpayer's real books. Review the dry-run table "
            "with the accountant first, then re-run with --env prod --yes-production.", 2)
    invoices, per_invoice, xml, code = prepare_classification(args, env)
    if args.confirm != code:
        raise MyDataError(
            f"Confirmation code mismatch: expected the code printed by `classify` for this exact "
            f"proposal and environment ({env}). The file, --mark filter or --env changed since the "
            "dry run, or the code is wrong. Run `classify` again and get the user's approval.", 2)
    print(plan_table(invoices, env, per_invoice), file=sys.stderr)
    params = {"postPerInvoice": "true"} if per_invoice else None
    results = parse_response_doc(call(env, "POST", "SendExpensesClassification", params, xml))
    emit_json({"env": env, "sent": len(invoices), "results": results})
    return 0 if results and all(r["statusCode"] == "Success" for r in results) else 7


# --------------------------------------------------------------------------- reconcile

ERP_ALIASES = {
    "mark": ("mark", "μαρκ", "marc", "invoice_mark", "invoicemark"),
    "afm": ("afm", "αφμ", "vat", "vat_number", "vatnumber", "counterpart_vat", "counterpartvat",
            "issuer_vat", "supplier_vat", "customer_vat", "tin"),
    "date": ("date", "issue_date", "issuedate", "ημερομηνία", "ημερομηνια", "ημ/νία"),
    "amount": ("amount", "gross", "gross_value", "grossvalue", "total", "σύνολο", "συνολο", "ποσό", "ποσο"),
    "ref": ("ref", "series_aa", "number", "doc_no", "αριθμός", "αριθμος", "παραστατικό", "παραστατικο"),
}


def normalise_afm(value):
    """Compare VAT numbers loosely: drop spaces/dots/dashes and a Greek EL/GR prefix,
    keep other country prefixes (foreign suppliers on 14.x documents)."""
    s = re.sub(r"[\s.\-]", "", str(value or "")).upper()
    return s[2:] if re.fullmatch(r"(EL|GR)\d{9}", s) else s


def read_erp_csv(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise MyDataError(f"{path}: empty CSV.", 2)
    colmap = {}
    for field in reader.fieldnames:
        key = (field or "").strip().lower()
        for canon, aliases in ERP_ALIASES.items():
            if key in aliases and canon not in colmap:
                colmap[canon] = field
    if "amount" not in colmap or ("mark" not in colmap and not {"afm", "date"} <= colmap.keys()):
        raise MyDataError(
            f"{path}: need an amount column plus either a MARK column or AFM + date columns. "
            f"Found columns: {', '.join(reader.fieldnames)}", 2)
    rows = []
    for n, r in enumerate(reader, start=2):
        get = lambda k: (r.get(colmap[k]) or "").strip() if k in colmap else ""  # noqa: E731
        if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue
        amount = parse_amount(get("amount"))
        rows.append({
            "line": n,
            "mark": re.sub(r"\D", "", get("mark")),
            "afm": normalise_afm(get("afm")),
            "date": parse_date(get("date")).isoformat() if get("date") else "",
            "amount": money(abs(amount)) if amount is not None else None,
            "ref": get("ref"),
        })
    return rows


def reconcile(erp_rows, invoices, side, tolerance=Decimal("0.01"), superseded=()):
    docs = []
    for inv in invoices:
        # The other party: the issuer on expenses (and on self-billed income, where the
        # customer issued the document), otherwise the counterpart.
        vat_role = "issuer" if side == "expenses" or is_self_billed(inv) else "counterpart"
        s = summarize_invoice(inv)
        gross = parse_amount(s["grossValue"]) if s["grossValue"] else None
        docs.append({"mark": s["mark"], "afm": normalise_afm(party_vat(inv, vat_role)), "date": s["issueDate"][:10],
                     "amount": money(abs(gross)) if gross is not None else None,
                     "invoiceType": s["invoiceType"], "series": s["series"], "aa": s["aa"],
                     "cancelled": bool(s["cancelledByMark"]), "used": False})
    by_mark = {d["mark"]: d for d in docs if d["mark"]}
    replaced = {s["mark"]: s["supersededBy"] for s in superseded}
    out = {"matched": [], "mismatched": [], "missingInMyData": [], "missingInErp": [],
           "cancelledInMyData": [], "superseded": list(superseded)}

    def same_amount(a, b):
        return a is not None and b is not None and abs(a - b) <= tolerance

    pending = []
    for row in erp_rows:
        doc = by_mark.get(row["mark"]) if row["mark"] else None
        diffs = []
        if doc is None and row["mark"] in replaced and replaced[row["mark"]] in by_mark:
            doc = by_mark[replaced[row["mark"]]]
            diffs.append(f"ERP has superseded MARK {row['mark']}; AADE kept {doc['mark']} (same invoice re-sent)")
        if doc is None:
            pending.append(row)
            continue
        doc["used"] = True
        if not same_amount(row["amount"], doc["amount"]):
            diffs.append(f"amount ERP {row['amount']} vs myDATA {doc['amount']}")
        if row["afm"] and doc["afm"] and row["afm"] != doc["afm"]:
            diffs.append(f"AFM ERP {row['afm']} vs myDATA {doc['afm']}")
        if row["date"] and doc["date"] and row["date"] != doc["date"]:
            diffs.append(f"date ERP {row['date']} vs myDATA {doc['date']}")
        if doc["cancelled"]:
            diffs.append("cancelled in myDATA")
        entry = {"erpLine": row["line"], "mark": doc["mark"], "matchedOn": "mark", **_pub(doc)}
        (out["mismatched"] if diffs else out["matched"]).append({**entry, "differences": diffs} if diffs else entry)
    for row in pending:
        candidates = [d for d in docs if not d["used"] and not d["cancelled"]
                      and (not row["afm"] or d["afm"] == row["afm"])
                      and (not row["date"] or d["date"] == row["date"])
                      and same_amount(row["amount"], d["amount"])]
        if len(candidates) == 1:
            d = candidates[0]
            d["used"] = True
            entry = {"erpLine": row["line"], "mark": d["mark"], "matchedOn": "afm+date+amount", **_pub(d)}
            if row["mark"]:
                out["mismatched"].append({**entry, "differences": [f"MARK ERP {row['mark']} vs myDATA {d['mark']}"]})
            else:
                out["matched"].append(entry)
        else:
            out["missingInMyData"].append({
                "erpLine": row["line"], "mark": row["mark"], "afm": row["afm"], "date": row["date"],
                "amount": row["amount"], "ref": row["ref"],
                "note": "several myDATA candidates; add the MARK to the ERP row" if candidates else ""})
    # Hint at near misses: same AFM and date, different amount, both otherwise unmatched.
    for miss in out["missingInMyData"]:
        if miss["note"] or not (miss["afm"] and miss["date"]):
            continue
        near = [d for d in docs if not d["used"] and not d["cancelled"]
                and d["afm"] == miss["afm"] and d["date"] == miss["date"]]
        if len(near) == 1:
            miss["note"] = (f"possible match: myDATA MARK {near[0]['mark']} has the same AFM and date "
                            f"but amount {near[0]['amount']}")
    for d in docs:
        if not d["used"]:
            (out["cancelledInMyData"] if d["cancelled"] else out["missingInErp"]).append(_pub(d))
    out["summary"] = {k: len(v) for k, v in out.items()}
    out["summary"].update({"erpRows": len(erp_rows), "myDataInvoices": len(docs)})
    return out


def _pub(doc):
    return {k: doc[k] for k in ("mark", "afm", "date", "amount", "invoiceType", "series", "aa")}


def cmd_reconcile(args):
    erp_rows = read_erp_csv(args.file)
    if args.mydata_json:
        invoices, env, seen = [], "file", set()
        for path in args.mydata_json:
            try:
                saved = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise MyDataError(f"Cannot read {path}: {e}", 2)
            items, env = saved.get("invoices", []), saved.get("env", env)
            if items and "mark" in items[0] and "invoiceHeader" not in items[0]:
                raise MyDataError("--mydata-json needs the full JSON from `docs`/`transmitted` "
                                  "(not --format summary/csv).", 2)
            method = saved.get("method", "")
            if method in ("RequestDocs", "RequestTransmittedDocs"):
                items = [i for i in items if book_side(i, method) == args.side]
            elif method.startswith("RequestDocs+"):  # `docs --include-self-entered`: already the expenses book
                items = items if args.side == "expenses" else []
            for i in items:
                if i.get("mark") not in seen:
                    seen.add(i.get("mark"))
                    invoices.append(i)
        invoices, superseded = drop_superseded(invoices)  # files saved before deduplication
    else:
        if not (args.date_from and args.date_to):
            raise MyDataError("reconcile needs --from and --to (or --mydata-json).", 2)
        env = get_env(args.env)
        args.mark, args.max_mark, args.inv_type, args.counterpart = 0, None, None, None
        book = book_invoices(env, args, args.side)
        invoices, superseded = book["invoices"], book["superseded"]
    if args.date_from and args.date_to:
        lo, hi = parse_date(args.date_from).isoformat(), parse_date(args.date_to).isoformat()
        erp_rows = [r for r in erp_rows if not r["date"] or lo <= r["date"] <= hi]
        invoices = [i for i in invoices if lo <= (i.get("invoiceHeader") or {}).get("issueDate", "")[:10] <= hi]
    result = reconcile(erp_rows, invoices, args.side, Decimal(str(args.tolerance)), superseded)
    emit_json({"env": env, "side": args.side, **result})
    return 0


# --------------------------------------------------------------------------- CLI


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", choices=sorted(BASE_URLS), help="test (default, or $MYDATA_ENV) or prod")
    common.add_argument("--entity", metavar="AFM", help="entityVatNumber: act for this AFM (accountants)")
    common.add_argument("--env-file", metavar="PATH",
                        help="load MYDATA_USER_ID / MYDATA_SUBSCRIPTION_KEY / MYDATA_ENV from this file")
    common.add_argument("--out", metavar="PATH", help="write the output to this file (mode 600) instead of stdout")

    p = argparse.ArgumentParser(prog="mydata.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION} (myDATA spec v{SPEC_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", parents=[common], help="check credentials and show the environment")

    for name, help_text in (("docs", "RequestDocs: documents others issued to you (your expenses side)"),
                            ("transmitted", "RequestTransmittedDocs: documents you transmitted")):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("--from", dest="date_from", help="issue date from (YYYY-MM-DD)")
        sp.add_argument("--to", dest="date_to", help="issue date to (YYYY-MM-DD)")
        sp.add_argument("--mark", default=0, type=int, help="return MARKs greater than this (default 0)")
        sp.add_argument("--max-mark", help="return MARKs up to this value")
        sp.add_argument("--inv-type", help="invoice type, e.g. 1.1 (table 8.1)")
        sp.add_argument("--counterpart", metavar="AFM", help="keep only invoices where this AFM is issuer or counterpart")
        sp.add_argument("--format", choices=("json", "summary", "csv"), default="json")
        if name == "docs":
            sp.add_argument("--include-self-entered", action="store_true",
                            help="expense book view: add your own 13.x/14.x/15.1/16.1 documents "
                                 "(from RequestTransmittedDocs), drop self-billed income and delivery notes")

    for name, help_text in (("income", "RequestMyIncome: income book totals"),
                            ("expenses", "RequestMyExpenses: expense book totals")):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("--from", dest="date_from", required=True)
        sp.add_argument("--to", dest="date_to", required=True)
        sp.add_argument("--counterpart", metavar="AFM", help="counterVatNumber filter")
        sp.add_argument("--inv-type")
        sp.add_argument("--format", choices=("json", "csv"), default="json")

    sp = sub.add_parser("classify", parents=[common],
                        help="validate a classification proposal and show what `send` would transmit (no writes)")
    sp.add_argument("--file", required=True, help="proposal JSON (see references/classify.md)")
    sp.add_argument("--mark", action="append", help="only this MARK from the file (repeatable)")
    sp.add_argument("--xml-only", action="store_true", help="print only the XML body")
    sp.add_argument("--xml-out", help="also write the XML body to this file")

    sp = sub.add_parser("send", parents=[common],
                        help="WRITE: SendExpensesClassification for a proposal approved after `classify`")
    sp.add_argument("--file", required=True, help="the same proposal JSON that `classify` showed")
    sp.add_argument("--mark", action="append", help="the same --mark filter used with `classify`")
    sp.add_argument("--confirm", required=True, metavar="CODE", help="confirmation code printed by `classify`")
    sp.add_argument("--yes-production", action="store_true", help="required with --env prod")

    sp = sub.add_parser("reconcile", parents=[common], help="match myDATA against an ERP/bank CSV")
    sp.add_argument("--file", required=True, help="ERP CSV: MARK and/or AFM, date, amount columns")
    sp.add_argument("--from", dest="date_from")
    sp.add_argument("--to", dest="date_to")
    sp.add_argument("--side", choices=("expenses", "income"), default="expenses",
                    help="expenses: received + own 13.x/14.x documents; income: issued + self-billed documents")
    sp.add_argument("--mydata-json", action="append",
                    help="saved `docs`/`transmitted` JSON instead of calling the API (repeatable)")
    sp.add_argument("--tolerance", default="0.01", help="amount tolerance in euro (default 0.01)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.env_file:
            load_env_file(args.env_file)
        if args.out:
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
                code = dispatch(args)
            print(f"wrote {args.out}", file=sys.stderr)
            return code
        return dispatch(args)
    except MyDataError as e:
        print(f"error: {redact(str(e))}", file=sys.stderr)
        return e.exit_code


def dispatch(args):
    commands = {
        "check": cmd_check,
        "docs": lambda a: cmd_docs(a, "RequestDocs"),
        "transmitted": lambda a: cmd_docs(a, "RequestTransmittedDocs"),
        "income": lambda a: cmd_book(a, "RequestMyIncome"),
        "expenses": lambda a: cmd_book(a, "RequestMyExpenses"),
        "classify": cmd_classify,
        "send": cmd_send,
        "reconcile": cmd_reconcile,
    }
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
