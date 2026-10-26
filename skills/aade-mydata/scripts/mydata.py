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
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

VERSION = "0.1.0"
SPEC_VERSION = "2.0.2"

BASE_URLS = {
    # Development/test environment, from AADE's "test URLs" list.
    "test": "https://mydataapidev.aade.gr/",
    # Production, from the official ERP technical description v2.0.2, section 4.2.
    "prod": "https://mydatapi.aade.gr/myDATA/",
}

ECLS_NS = "https://www.aade.gr/myDATA/expensesClassificaton/v1.0"  # sic, AADE's spelling
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
    "cancelledByMark", "classified",
]

MAX_PAGES = 500


class MyDataError(Exception):
    """An error with a message that is safe to show (never contains credentials)."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- config


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
    status, data = _transport(method, url, headers, body)
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
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise MyDataError(f"myDATA returned XML that could not be parsed: {e}")


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
    if args.counterpart:
        result["invoices"] = [i for i in result["invoices"] if args.counterpart in (
            party_vat(i, "issuer"), party_vat(i, "counterpart"))]
    return {"env": env, "method": endpoint, "specVersion": SPEC_VERSION, "pages": pages,
            "params": {k: v for k, v in params.items() if v}, **result}


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


def cmd_docs(args, endpoint):
    result = request_docs(get_env(args.env), endpoint, args)
    if args.format == "csv":
        write_csv([summarize_invoice(i) for i in result["invoices"]], SUMMARY_FIELDS, sys.stdout)
    elif args.format == "summary":
        emit_json({k: v for k, v in result.items() if k != "invoices"} |
                  {"invoices": [summarize_invoice(i) for i in result["invoices"]]})
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


def cmd_classify(args):
    env = get_env(args.env)
    if args.send and env == "prod" and not args.yes_production:
        raise MyDataError(
            "Refusing to send to PRODUCTION without --yes-production.\n"
            "Production classifications update the taxpayer's real books. Review the dry-run table "
            "with the accountant first, then re-run with --env prod --send --yes-production.", 2)
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
    if args.xml_out:
        Path(args.xml_out).write_bytes(xml)
    if not args.send:
        if args.xml_only:
            sys.stdout.write(xml.decode("utf-8"))
        else:
            print("DRY RUN - nothing sent. Review this with the user (and their accountant):\n")
            print(plan_table(invoices, env, per_invoice))
            print("\nXML body that --send would POST:\n")
            sys.stdout.write(xml.decode("utf-8"))
        return 0
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
            "afm": re.sub(r"\D", "", get("afm")),
            "date": parse_date(get("date")).isoformat() if get("date") else "",
            "amount": money(abs(amount)) if amount is not None else None,
            "ref": get("ref"),
        })
    return rows


def reconcile(erp_rows, invoices, side, tolerance=Decimal("0.01")):
    vat_role = "issuer" if side == "expenses" else "counterpart"
    docs = []
    for inv in invoices:
        s = summarize_invoice(inv)
        gross = parse_amount(s["grossValue"]) if s["grossValue"] else None
        docs.append({"mark": s["mark"], "afm": party_vat(inv, vat_role), "date": s["issueDate"][:10],
                     "amount": money(abs(gross)) if gross is not None else None,
                     "invoiceType": s["invoiceType"], "series": s["series"], "aa": s["aa"],
                     "cancelled": bool(s["cancelledByMark"]), "used": False})
    by_mark = {d["mark"]: d for d in docs if d["mark"]}
    out = {"matched": [], "mismatched": [], "missingInMyData": [], "missingInErp": [], "cancelledInMyData": []}

    def same_amount(a, b):
        return a is not None and b is not None and abs(a - b) <= tolerance

    pending = []
    for row in erp_rows:
        doc = by_mark.get(row["mark"]) if row["mark"] else None
        if doc is None:
            pending.append(row)
            continue
        doc["used"] = True
        diffs = []
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
        try:
            saved = json.loads(Path(args.mydata_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise MyDataError(f"Cannot read {args.mydata_json}: {e}", 2)
        invoices, env = saved.get("invoices", []), saved.get("env", "file")
        if invoices and "mark" in invoices[0] and "invoiceHeader" not in invoices[0]:
            raise MyDataError("--mydata-json needs the full JSON from `docs`/`transmitted` "
                              "(not --format summary/csv).", 2)
    else:
        if not (args.date_from and args.date_to):
            raise MyDataError("reconcile needs --from and --to (or --mydata-json).", 2)
        env = get_env(args.env)
        endpoint = "RequestDocs" if args.side == "expenses" else "RequestTransmittedDocs"
        args.mark, args.max_mark, args.inv_type, args.counterpart = 0, None, None, None
        invoices = request_docs(env, endpoint, args)["invoices"]
    if args.date_from and args.date_to:
        lo, hi = parse_date(args.date_from).isoformat(), parse_date(args.date_to).isoformat()
        erp_rows = [r for r in erp_rows if not r["date"] or lo <= r["date"] <= hi]
        invoices = [i for i in invoices if lo <= (i.get("invoiceHeader") or {}).get("issueDate", "")[:10] <= hi]
    result = reconcile(erp_rows, invoices, args.side, Decimal(str(args.tolerance)))
    emit_json({"env": env, "side": args.side, **result})
    return 0


# --------------------------------------------------------------------------- CLI


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", choices=sorted(BASE_URLS), help="test (default, or $MYDATA_ENV) or prod")
    common.add_argument("--entity", metavar="AFM", help="entityVatNumber: act for this AFM (accountants)")

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

    for name, help_text in (("income", "RequestMyIncome: income book totals"),
                            ("expenses", "RequestMyExpenses: expense book totals")):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("--from", dest="date_from", required=True)
        sp.add_argument("--to", dest="date_to", required=True)
        sp.add_argument("--counterpart", metavar="AFM", help="counterVatNumber filter")
        sp.add_argument("--inv-type")
        sp.add_argument("--format", choices=("json", "csv"), default="json")

    sp = sub.add_parser("classify", parents=[common], help="SendExpensesClassification (dry run unless --send)")
    sp.add_argument("--file", required=True, help="proposal JSON (see references/classify.md)")
    sp.add_argument("--mark", action="append", help="only this MARK from the file (repeatable)")
    sp.add_argument("--send", action="store_true", help="actually transmit (default: dry run)")
    sp.add_argument("--yes-production", action="store_true", help="required with --env prod --send")
    sp.add_argument("--xml-only", action="store_true", help="dry run prints only the XML body")
    sp.add_argument("--xml-out", help="also write the XML body to this file")

    sp = sub.add_parser("reconcile", parents=[common], help="match myDATA against an ERP/bank CSV")
    sp.add_argument("--file", required=True, help="ERP CSV: MARK and/or AFM, date, amount columns")
    sp.add_argument("--from", dest="date_from")
    sp.add_argument("--to", dest="date_to")
    sp.add_argument("--side", choices=("expenses", "income"), default="expenses",
                    help="expenses: RequestDocs (received); income: RequestTransmittedDocs (issued)")
    sp.add_argument("--mydata-json", help="use saved `docs`/`transmitted` JSON instead of calling the API")
    sp.add_argument("--tolerance", default="0.01", help="amount tolerance in euro (default 0.01)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "docs":
            return cmd_docs(args, "RequestDocs")
        if args.cmd == "transmitted":
            return cmd_docs(args, "RequestTransmittedDocs")
        if args.cmd == "income":
            return cmd_book(args, "RequestMyIncome")
        if args.cmd == "expenses":
            return cmd_book(args, "RequestMyExpenses")
        if args.cmd == "classify":
            return cmd_classify(args)
        if args.cmd == "reconcile":
            return cmd_reconcile(args)
    except MyDataError as e:
        print(f"error: {redact(str(e))}", file=sys.stderr)
        return e.exit_code
    return 2


if __name__ == "__main__":
    sys.exit(main())
