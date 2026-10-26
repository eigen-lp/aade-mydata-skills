#!/usr/bin/env python3
"""Dev only: send a few synthetic expense documents to the myDATA TEST environment.

Uses recipient-entered document types (13.1, 14.4) so a single test user can create
invoices that land in its own expense books, then exercise `classify` and `reconcile`.
Always targets https://mydataapidev.aade.gr/ regardless of MYDATA_ENV. Not part of the skill.

    set -a; . ./.env.dev; set +a   # this dev tool reads credentials from the environment
    python3 tools/seed_test_invoices.py                 # print the XML only (AFM from $MYDATA_TEST_AFM)
    python3 tools/seed_test_invoices.py --send --erp-out erp.csv
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mydata", ROOT / "skills/aade-mydata/scripts/mydata.py")
mydata = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mydata)

INV_NS = "http://www.aade.gr/myDATA/invoice/v1.0"
ECLS_NS = mydata.ECLS_NS

# Synthetic suppliers. 123456783 passes the Greek AFM checksum but is not a known company.
SEEDS = [
    {"type": "14.4", "issuer": {"vatNumber": "US987654321", "country": "US", "name": "Example Cloud Inc."},
     "net": "50.00", "vatCategory": 7, "vatExemptionCategory": 1, "vat": "0.00",
     "note": "Third-country SaaS subscription"},
    {"type": "13.1", "issuer": {"vatNumber": "123456783", "country": "GR"},
     "net": "40.00", "vatCategory": 1, "vat": "9.60",
     "note": "Domestic retail purchase (office supplies)"},
]


def add_placeholder_classification(parent, q, amount):
    ec = ET.SubElement(parent, q("expensesClassification"))
    ET.SubElement(ec, f"{{{ECLS_NS}}}classificationType").text = "E3_585_016"
    ET.SubElement(ec, f"{{{ECLS_NS}}}classificationCategory").text = "category2_5"
    ET.SubElement(ec, f"{{{ECLS_NS}}}amount").text = amount


def build_invoices(afm, series, aa_base, issue_date):
    ET.register_namespace("", INV_NS)
    ET.register_namespace("ecls", ECLS_NS)
    q = lambda n: f"{{{INV_NS}}}{n}"  # noqa: E731
    root = ET.Element(q("InvoicesDoc"))
    for i, s in enumerate(SEEDS):
        inv = ET.SubElement(root, q("invoice"))
        issuer = ET.SubElement(inv, q("issuer"))
        ET.SubElement(issuer, q("vatNumber")).text = s["issuer"]["vatNumber"]
        ET.SubElement(issuer, q("country")).text = s["issuer"]["country"]
        ET.SubElement(issuer, q("branch")).text = "0"
        if s["issuer"]["country"] != "GR":
            ET.SubElement(issuer, q("name")).text = s["issuer"]["name"]
            addr = ET.SubElement(issuer, q("address"))
            ET.SubElement(addr, q("postalCode")).text = "94105"
            ET.SubElement(addr, q("city")).text = "San Francisco"
        cp = ET.SubElement(inv, q("counterpart"))
        ET.SubElement(cp, q("vatNumber")).text = afm
        ET.SubElement(cp, q("country")).text = "GR"
        ET.SubElement(cp, q("branch")).text = "0"
        cp_addr = ET.SubElement(cp, q("address"))  # mandatory for 14.x (AADE error 204)
        ET.SubElement(cp_addr, q("postalCode")).text = "10431"
        ET.SubElement(cp_addr, q("city")).text = "Athens"
        head = ET.SubElement(inv, q("invoiceHeader"))
        ET.SubElement(head, q("series")).text = series
        ET.SubElement(head, q("aa")).text = str(aa_base + i)
        ET.SubElement(head, q("issueDate")).text = issue_date
        ET.SubElement(head, q("invoiceType")).text = s["type"]
        ET.SubElement(head, q("currency")).text = "EUR"
        gross = f"{float(s['net']) + float(s['vat']):.2f}"
        # No paymentMethods: forbidden for recipient-entered types (AADE error 205).
        row = ET.SubElement(inv, q("invoiceDetails"))
        ET.SubElement(row, q("lineNumber")).text = "1"
        ET.SubElement(row, q("netValue")).text = s["net"]
        ET.SubElement(row, q("vatCategory")).text = str(s["vatCategory"])
        ET.SubElement(row, q("vatAmount")).text = s["vat"]
        if "vatExemptionCategory" in s:
            ET.SubElement(row, q("vatExemptionCategory")).text = str(s["vatExemptionCategory"])
        # Recipient-entered types must carry an expense classification per line (AADE error 230).
        # A generic placeholder; the classify demo then replaces it via SendExpensesClassification.
        # category2_5 = VAT not deductible, so the E3 amount includes the VAT (AADE error 306).
        add_placeholder_classification(row, q, gross)
        summ = ET.SubElement(inv, q("invoiceSummary"))
        for tag, val in (("totalNetValue", s["net"]), ("totalVatAmount", s["vat"]),
                         ("totalWithheldAmount", "0.00"), ("totalFeesAmount", "0.00"),
                         ("totalStampDutyAmount", "0.00"), ("totalOtherTaxesAmount", "0.00"),
                         ("totalDeductionsAmount", "0.00"), ("totalGrossValue", gross)):
            ET.SubElement(summ, q(tag)).text = val
        add_placeholder_classification(summ, q, gross)  # must mirror the rows (AADE errors 311/321)
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--afm", default=os.environ.get("MYDATA_TEST_AFM"),
                   help="AFM your TEST user is registered under (default: $MYDATA_TEST_AFM)")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--send", action="store_true", help="POST to the test environment (default: print XML)")
    p.add_argument("--erp-out", help="write a sample ERP CSV for these invoices (one amount deliberately off)")
    args = p.parse_args()
    if not re.fullmatch(r"\d{9}", args.afm or ""):
        sys.exit("--afm must be 9 digits")
    series, aa_base = "SEED", int(time.time()) % 10_000_000
    xml = build_invoices(args.afm, series, aa_base, args.date)
    if not args.send:
        sys.stdout.write(xml.decode())
        return 0
    try:
        data = mydata.call("test", "POST", "SendInvoices", None, xml)  # hard-wired to TEST
        results = mydata.parse_response_doc(data)
    except mydata.MyDataError as e:
        sys.exit(f"error: {mydata.redact(str(e))}")
    seeded = []
    for s, r in zip(SEEDS, results):
        seeded.append({"invType": s["type"], "note": s["note"], "issuerVat": s["issuer"]["vatNumber"],
                       "net": s["net"], "vat": s["vat"], **r})
    print(json.dumps({"env": "test", "series": series, "seeded": seeded}, ensure_ascii=False, indent=2))
    ok = [x for x in seeded if x["statusCode"] == "Success"]
    if args.erp_out and ok:
        lines = ["mark;afm;date;amount;ref"]
        for n, x in enumerate(ok):
            gross = float(x["net"]) + float(x["vat"])
            if n == 1:
                gross += 1  # deliberate mismatch for the reconcile demo
            lines.append(f"{x['invoiceMark'] if n == 0 else ''};{x['issuerVat']};{args.date};"
                         f"{gross:.2f}".replace(".", ",") + f";{series}-{n}")
        lines.append(f";999999999;{args.date};12,34;NOT-IN-MYDATA")
        Path(args.erp_out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not seeded:
        sys.exit("error: no response entries parsed from SendInvoices")
    for x in seeded:
        for e in x["errors"]:
            print(f"{x['invType']}: [{e['code']}] {e['message']}", file=sys.stderr)
    return 0 if len(ok) == len(seeded) else 1


if __name__ == "__main__":
    sys.exit(main())
