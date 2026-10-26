"""Offline tests for skills/aade-mydata/scripts/mydata.py.

No network: every HTTP call goes through mydata._transport, which these tests replace.
Set AADE_XSD_DIR to a folder with the official v2.0.2 XSDs to also run the schema check.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
SCRIPT = ROOT / "skills" / "aade-mydata" / "scripts" / "mydata.py"

spec = importlib.util.spec_from_file_location("mydata", SCRIPT)
mydata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mydata)

FAKE_USER = "fake-user-1234"
FAKE_KEY = "not-a-real-subscription-key"


class FakeAADE:
    """Records requests and answers from fixtures keyed by endpoint (+ page)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, body=None, timeout=60):
        parts = urllib.parse.urlsplit(url)
        endpoint = parts.path.rsplit("/", 1)[-1]
        params = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append({"method": method, "url": url, "endpoint": endpoint,
                           "params": params, "headers": headers, "body": body})
        route = self.routes[endpoint]
        if callable(route):
            return route(params, body)
        if isinstance(route, list):  # paged: pick by nextPartitionKey presence
            return 200, (FIX / route[1 if "nextPartitionKey" in params else 0]).read_bytes()
        return 200, (FIX / route).read_bytes()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("MYDATA_USER_ID", FAKE_USER)
    monkeypatch.setenv("MYDATA_SUBSCRIPTION_KEY", FAKE_KEY)
    monkeypatch.delenv("MYDATA_ENV", raising=False)


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be used")
    monkeypatch.setattr(mydata, "_transport", boom)


def install(monkeypatch, routes):
    fake = FakeAADE(routes)
    monkeypatch.setattr(mydata, "_transport", fake)
    return fake


def run(capsys, *argv):
    code = mydata.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


DOCS_ROUTES = {"RequestDocs": ["requested_docs_page1.xml", "requested_docs_page2.xml"],
               "RequestTransmittedDocs": ["requested_docs_page1.xml", "requested_docs_page2.xml"]}


# --------------------------------------------------------------------------- read


def test_docs_follows_paging_and_sends_auth_headers(creds, monkeypatch, capsys):
    fake = install(monkeypatch, DOCS_ROUTES)
    code, out, err = run(capsys, "docs", "--from", "2026-10-01", "--to", "2026-10-31")
    assert code == 0, err
    data = json.loads(out)
    assert [i["mark"] for i in data["invoices"]] == [
        "400000000000101", "400000000000102", "400000000000103", "400000000000104"]
    assert data["pages"] == 2 and data["env"] == "test"
    assert len(data["cancelledInvoices"]) == 1
    first, second = fake.calls
    assert first["url"].startswith("https://mydataapidev.aade.gr/RequestDocs?")
    assert first["params"] == {"mark": "0", "dateFrom": "01/10/2026", "dateTo": "31/10/2026"}
    assert second["params"]["nextPartitionKey"] == "PK-0001"
    assert second["params"]["nextRowKey"] == "RK-0002"
    assert first["headers"]["aade-user-id"] == FAKE_USER
    assert first["headers"]["Ocp-Apim-Subscription-Key"] == FAKE_KEY
    assert FAKE_KEY not in out + err and FAKE_USER not in out + err


def test_docs_csv_and_counterpart_filter(creds, monkeypatch, capsys):
    install(monkeypatch, DOCS_ROUTES)
    code, out, _ = run(capsys, "docs", "--format", "csv", "--counterpart", "999999999")
    assert code == 0
    lines = out.strip().splitlines()
    assert lines[0].split(",")[:3] == ["mark", "issueDate", "invoiceType"]
    # 999999999 issued 101 and 103; 888888888 is the counterpart of all, so filter on issuer AFM.
    assert [l.split(",")[0] for l in lines[1:]] == ["400000000000101", "400000000000103"]


def test_transmitted_uses_its_endpoint_and_prod_url(creds, monkeypatch, capsys):
    fake = install(monkeypatch, DOCS_ROUTES)
    code, _, _ = run(capsys, "transmitted", "--env", "prod", "--mark", "5")
    assert code == 0
    assert fake.calls[0]["url"].startswith("https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs?")
    assert fake.calls[0]["params"]["mark"] == "5"


def test_env_var_selects_environment(creds, monkeypatch, capsys):
    monkeypatch.setenv("MYDATA_ENV", "prod")
    fake = install(monkeypatch, DOCS_ROUTES)
    run(capsys, "docs")
    assert fake.calls[0]["url"].startswith("https://mydatapi.aade.gr/myDATA/")


def test_aade_sample_classification_response_parses(creds, monkeypatch, capsys):
    install(monkeypatch, {"RequestDocs": "expenses_classification_response.xml"})
    code, out, _ = run(capsys, "docs")
    assert code == 0
    ecls = json.loads(out)["expensesClassifications"]
    assert ecls[0]["transactionMode"] == "2"
    assert ecls[0]["classificationMark"] == "400001912586845"


def test_expenses_book_paging_csv(creds, monkeypatch, capsys):
    fake = install(monkeypatch, {"RequestMyExpenses": ["requested_book_info.xml", "requested_book_info_page2.xml"]})
    code, out, _ = run(capsys, "expenses", "--from", "2026-10-01", "--to", "31/10/2026",
                       "--counterpart", "999999999", "--format", "csv")
    assert code == 0
    assert fake.calls[0]["params"] == {"dateFrom": "01/10/2026", "dateTo": "31/10/2026",
                                       "counterVatNumber": "999999999"}
    assert fake.calls[1]["params"]["nextPartitionKey"] == "PK-B1"
    rows = out.strip().splitlines()
    assert rows[0].startswith("issueDate,invType,counterVatNumber")
    assert len(rows) == 3


def test_income_json(creds, monkeypatch, capsys):
    install(monkeypatch, {"RequestMyIncome": ["requested_book_info.xml", "requested_book_info_page2.xml"]})
    code, out, _ = run(capsys, "income", "--from", "2026-10-01", "--to", "2026-10-31")
    assert code == 0
    data = json.loads(out)
    assert data["method"] == "RequestMyIncome" and len(data["rows"]) == 2
    assert data["rows"][0]["grossValue"] == "124"


def test_paging_stops_on_repeated_token(creds, monkeypatch, capsys):
    fake = install(monkeypatch, {"RequestDocs": lambda p, b: (200, (FIX / "requested_docs_page1.xml").read_bytes())})
    code, _, _ = run(capsys, "docs")
    assert code == 0 and len(fake.calls) == 2


def test_check_reports_without_printing_secrets(creds, monkeypatch, capsys):
    install(monkeypatch, {"RequestMyIncome": "requested_book_info_page2.xml"})
    code, out, _ = run(capsys, "check")
    assert code == 0
    assert "environment: test" in out and "MYDATA_SUBSCRIPTION_KEY: set" in out
    assert FAKE_KEY not in out and FAKE_USER not in out


# --------------------------------------------------------------------------- errors & secrets


def test_missing_credentials(monkeypatch, capsys, no_network):
    monkeypatch.delenv("MYDATA_USER_ID", raising=False)
    monkeypatch.delenv("MYDATA_SUBSCRIPTION_KEY", raising=False)
    code, _, err = run(capsys, "docs")
    assert code == 3
    assert "MYDATA_USER_ID" in err and "MYDATA_SUBSCRIPTION_KEY" in err


def test_http_401_message(creds, monkeypatch, capsys):
    install(monkeypatch, {"RequestDocs": lambda p, b: (401, b"Access denied due to invalid subscription key.")})
    code, _, err = run(capsys, "docs")
    assert code == 4 and "credentials rejected" in err


def test_error_bodies_are_redacted(creds, monkeypatch, capsys):
    echo = f"bad request for user {FAKE_USER} key {FAKE_KEY}".encode()
    install(monkeypatch, {"RequestDocs": lambda p, b: (500, echo)})
    code, out, err = run(capsys, "docs")
    assert code == 5
    assert FAKE_KEY not in out + err and FAKE_USER not in out + err
    assert "<MYDATA_SUBSCRIPTION_KEY>" in err


def test_doctype_is_refused(creds, monkeypatch, capsys):
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><RequestedDoc>&x;</RequestedDoc>'
    install(monkeypatch, {"RequestDocs": lambda p, b: (200, evil)})
    code, _, err = run(capsys, "docs")
    assert code == 1 and "DOCTYPE" in err


# --------------------------------------------------------------------------- classify


def test_classify_dry_run_does_not_touch_network(monkeypatch, capsys, no_network):
    monkeypatch.delenv("MYDATA_USER_ID", raising=False)
    code, out, err = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"))
    assert code == 0, err
    assert out.startswith("DRY RUN")
    assert "| 400000000000101 | 1.1" in out
    xml = out[out.index("<?xml"):]
    root = ET.fromstring(xml.encode())
    ns = {"e": mydata.ECLS_NS}
    assert root.tag == f"{{{mydata.ECLS_NS}}}ExpensesClassificationsDoc"
    marks = [m.text for m in root.findall("e:expensesInvoiceClassification/e:invoiceMark", ns)]
    assert marks == ["400000000000101", "400000000000102"]
    amounts = [a.text for a in root.iter(f"{{{mydata.ECLS_NS}}}amount")]
    assert amounts == ["100.00", "100.00", "250.00", "250.00"]


def test_classify_lines_and_reject(no_network, capsys):
    code, out, _ = run(capsys, "classify", "--file", str(FIX / "proposal_lines.json"), "--xml-only")
    assert code == 0
    root = ET.fromstring(out.encode())
    ns = {"e": mydata.ECLS_NS}
    second = root.findall("e:expensesInvoiceClassification", ns)[1]
    assert second.find("e:transactionMode", ns).text == "1"
    assert second.find("e:invoicesExpensesClassificationDetails", ns) is None


def test_classify_mark_filter(no_network, capsys):
    code, out, _ = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"),
                       "--mark", "400000000000102", "--xml-only")
    assert code == 0
    assert "400000000000101" not in out and "400000000000102" in out


def test_classify_rejects_bad_combination(no_network, capsys):
    code, out, err = run(capsys, "classify", "--file", str(FIX / "proposal_invalid.json"))
    assert code == 6 and out == ""
    assert "E3_585_016 is not allowed with category2_1 on invoice type 1.1" in err
    assert "needs vatCategory and vatAmount" in err


def test_production_send_requires_explicit_flag(monkeypatch, capsys, no_network):
    # Refuses before reading credentials or touching the network.
    monkeypatch.delenv("MYDATA_USER_ID", raising=False)
    code, out, err = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"),
                         "--env", "prod", "--send")
    assert code == 2 and out == ""
    assert "--yes-production" in err


def test_production_send_refused_via_env_var(monkeypatch, capsys, no_network):
    monkeypatch.setenv("MYDATA_ENV", "prod")
    code, _, err = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"), "--send")
    assert code == 2 and "--yes-production" in err


def test_send_posts_same_xml_as_dry_run(creds, monkeypatch, capsys):
    _, dry, _ = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"), "--xml-only")
    fake = install(monkeypatch, {"SendExpensesClassification": "response_success.xml"})
    code, out, err = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"), "--send")
    assert code == 0, err
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://mydataapidev.aade.gr/SendExpensesClassification?postPerInvoice=true"
    assert call["body"].decode() == dry
    assert call["headers"]["Content-Type"] == "application/xml"
    result = json.loads(out)
    assert result["results"][0]["statusCode"] == "Success"
    assert result["results"][0]["classificationMark"] == "400000000000501"
    assert "SendExpensesClassification on TEST" in err


def test_send_lines_mode_has_no_post_per_invoice(creds, monkeypatch, capsys):
    fake = install(monkeypatch, {"SendExpensesClassification": "response_success.xml"})
    run(capsys, "classify", "--file", str(FIX / "proposal_lines.json"), "--send")
    assert "postPerInvoice" not in fake.calls[0]["url"]


def test_send_reports_validation_errors(creds, monkeypatch, capsys):
    install(monkeypatch, {"SendExpensesClassification": "response_error.xml"})
    code, out, _ = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"), "--send")
    assert code == 7
    res = json.loads(out)["results"][0]
    assert res["statusCode"] == "ValidationError" and res["errors"][0]["code"] == "215"


def test_production_send_with_flag_uses_prod_url(creds, monkeypatch, capsys):
    fake = install(monkeypatch, {"SendExpensesClassification": "response_success.xml"})
    code, _, _ = run(capsys, "classify", "--file", str(FIX / "proposal_per_invoice.json"),
                     "--env", "prod", "--send", "--yes-production")
    assert code == 0
    assert fake.calls[0]["url"].startswith("https://mydatapi.aade.gr/myDATA/SendExpensesClassification")


@pytest.mark.parametrize("proposal, message", [
    ({"invoices": []}, "non-empty 'invoices'"),
    ({"invoices": [{"mark": "abc", "lines": []}]}, "positive integer"),
    ({"invoices": [{"mark": 1, "classifications": [{"type": "E3_102_001", "category": "category2_1", "amount": 1}]}]},
     "postPerInvoice"),
    ({"invoices": [{"mark": 1, "transactionMode": 3}]}, "transactionMode must be 1"),
    ({"invoices": [{"mark": 1, "lines": [{"lineNumber": 1, "classifications": [
        {"type": "E3_102_001", "category": "category2_1", "amount": "-5"}]}]}]}, ">= 0"),
    ({"invoices": [{"mark": 1, "lines": [{"lineNumber": 1, "classifications": [
        {"type": "E3_102_001", "category": "category2_1", "amount": "1.005"}]}]}]}, "more than 2 decimals"),
    ({"invoices": [{"mark": 1, "lines": [{"lineNumber": 1, "classifications": [
        {"type": "E3_102_001", "amount": "1"}]}]}]}, "needs a category"),
    ({"invoices": [{"mark": 1, "invType": "11.1", "lines": [{"lineNumber": 1, "classifications": [
        {"type": "E3_102_001", "category": "category2_1", "amount": "1"}]}]}]}, "lists no expense"),
    ({"invoices": [{"mark": 1, "invType": "1.1", "lines": [{"lineNumber": 1, "classifications": [
        {"type": "E3_102_001", "category": "category2_95", "amount": "1"}]}]}]}, "does not update E3"),
    ({"postPerInvoice": True, "invoices": [{"mark": 1, "invType": "5.2", "classifications": [
        {"category": "category2_9", "amount": "10"},
        {"type": "VAT_361", "amount": "10", "vatAmount": "2.40", "vatCategory": 1}]}]}, "category2_9"),
    ({"entityVatNumber": "12", "invoices": [{"mark": 1, "transactionMode": 1}]}, "9-digit"),
])
def test_proposal_validation(proposal, message):
    _, errors, _ = mydata.normalise_proposal(proposal)
    assert any(message in e for e in errors), errors


def test_any_above_category_accepts_listed_types():
    _, errors, _ = mydata.normalise_proposal({"invoices": [{"mark": 1, "invType": "1.1", "lines": [
        {"lineNumber": 1, "classifications": [{"type": "E3_585_016", "category": "category2_10", "amount": "5"}]}]}]})
    assert errors == []


def test_combinations_table_from_aade_spreadsheet():
    combos = json.loads((SCRIPT.parent.parent / "references" / "expense-combinations.json").read_text())
    assert "aade.gr" in combos["_source"]["url"]
    types = combos["invoiceTypes"]
    assert "E3_102_001" in types["1.1"]["expenses"]["category2_1"]
    assert types["17.1"]["expenses"]["category2_6"] == ["E3_581_001", "E3_581_002", "E3_581_003"]
    assert types["11.1"]["expenses"] is None
    assert types["5.1"]["expenses"] == "correlated"


# --------------------------------------------------------------------------- reconcile


def saved_docs(tmp_path, creds_env, monkeypatch, capsys):
    install(monkeypatch, DOCS_ROUTES)
    _, out, _ = run(capsys, "docs")
    path = tmp_path / "docs.json"
    path.write_text(out)
    return path


def test_reconcile_against_saved_docs(tmp_path, creds, monkeypatch, capsys):
    docs = saved_docs(tmp_path, creds, monkeypatch, capsys)
    monkeypatch.setattr(mydata, "_transport", lambda *a, **k: pytest.fail("network used"))
    code, out, err = run(capsys, "reconcile", "--file", str(FIX / "erp.csv"), "--mydata-json", str(docs))
    assert code == 0, err
    r = json.loads(out)
    assert r["summary"]["matched"] == 2
    assert {m["matchedOn"] for m in r["matched"]} == {"mark", "afm+date+amount"}
    assert r["mismatched"][0]["mark"] == "400000000000102"
    assert "amount ERP 300.00 vs myDATA 310.00" in r["mismatched"][0]["differences"]
    assert r["missingInMyData"][0]["mark"] == "400000000000999"
    assert r["cancelledInMyData"][0]["mark"] == "400000000000103"
    assert r["missingInErp"] == []


def test_reconcile_lists_invoices_missing_from_erp(tmp_path, creds, monkeypatch, capsys):
    docs = saved_docs(tmp_path, creds, monkeypatch, capsys)
    erp = tmp_path / "erp.csv"
    erp.write_text("mark,amount\n400000000000101,124.00\n")
    code, out, _ = run(capsys, "reconcile", "--file", str(erp), "--mydata-json", str(docs),
                       "--from", "2026-10-01", "--to", "2026-10-15")
    assert code == 0
    r = json.loads(out)
    assert r["summary"]["matched"] == 1
    assert [d["mark"] for d in r["missingInErp"]] == ["400000000000102"]  # 103/104 are outside the dates


def test_reconcile_live_uses_request_docs(creds, monkeypatch, capsys):
    fake = install(monkeypatch, DOCS_ROUTES)
    code, out, _ = run(capsys, "reconcile", "--file", str(FIX / "erp.csv"), "--from", "2026-10-01", "--to", "2026-10-31")
    assert code == 0 and fake.calls[0]["endpoint"] == "RequestDocs"
    assert json.loads(out)["summary"]["matched"] == 2


def test_reconcile_income_side_uses_transmitted(creds, monkeypatch, capsys):
    fake = install(monkeypatch, DOCS_ROUTES)
    run(capsys, "reconcile", "--side", "income", "--file", str(FIX / "erp.csv"),
        "--from", "2026-10-01", "--to", "2026-10-31")
    assert fake.calls[0]["endpoint"] == "RequestTransmittedDocs"


def test_reconcile_rejects_summary_json(tmp_path, creds, monkeypatch, capsys):
    install(monkeypatch, DOCS_ROUTES)
    _, out, _ = run(capsys, "docs", "--format", "summary")
    path = tmp_path / "summary.json"
    path.write_text(out)
    code, _, err = run(capsys, "reconcile", "--file", str(FIX / "erp.csv"), "--mydata-json", str(path))
    assert code == 2 and "full JSON" in err


def test_reconcile_needs_usable_columns(tmp_path, capsys, no_network):
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n")
    code, _, err = run(capsys, "reconcile", "--file", str(bad), "--mydata-json", str(bad))
    assert code == 2 and "amount column" in err


@pytest.mark.parametrize("raw, safe", [
    ('=HYPERLINK("http://x","y")', '\'=HYPERLINK("http://x","y")'), ("+1+1", "'+1+1"), ("@SUM(A1)", "'@SUM(A1)"),
    ("-3.00", "-3.00"), ("ACME A.E.", "ACME A.E."), ({"a": 1}, ""),
])
def test_csv_cells_are_formula_safe(raw, safe):
    assert mydata.csv_safe(raw) == safe


@pytest.mark.parametrize("text, value", [
    ("1234.56", "1234.56"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
    ("12,5", "12.5"), ("€ 10", "10"), ("-3,00", "-3.00"),
])
def test_parse_amount(text, value):
    assert mydata.parse_amount(text) == Decimal(value)


# --------------------------------------------------------------------------- optional XSD check


@pytest.mark.skipif(not os.environ.get("AADE_XSD_DIR") or not shutil.which("xmllint"),
                    reason="set AADE_XSD_DIR to the official v2.0.2 XSD folder and install xmllint")
@pytest.mark.parametrize("proposal", ["proposal_per_invoice.json", "proposal_lines.json"])
def test_dry_run_xml_validates_against_official_xsd(proposal, tmp_path, capsys):
    out_file = tmp_path / "body.xml"
    code, _, _ = run(capsys, "classify", "--file", str(FIX / proposal), "--xml-out", str(out_file))
    assert code == 0
    xsd = Path(os.environ["AADE_XSD_DIR"]) / "expensesClassification-v2.0.2.xsd"
    res = subprocess.run(["xmllint", "--noout", "--schema", str(xsd), str(out_file)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
