#!/usr/bin/env python3
"""Rebuild skills/aade-mydata/references/expense-combinations.json.

Reads AADE's official "characterization combinations" spreadsheet
(syndiasmoi_xaraktirismwn_*.xlsx) and keeps the EXPENSE side only:
invoice type -> expense category -> allowed E3 classification types.

Usage (maintainers only; needs network):
    python3 tools/build_combinations.py            # downloads the official file
    python3 tools/build_combinations.py path.xlsx  # or use a local copy

Stdlib only. The spreadsheet itself is not vendored in this repo.
"""
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

SOURCE_URL = (
    "https://www.aade.gr/sites/default/files/2026-09/"
    "syndiasmoi_xaraktirismwn_v2.0.2_0.xlsx"
)
SOURCE_PAGE = "https://www.aade.gr/en/mydata/technical-specifications-versions-mydata"
SPEC_VERSION = "v2.0.2 (official, September 2026)"
OUT = Path(__file__).resolve().parent.parent / "skills/aade-mydata/references/expense-combinations.json"

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_sheets(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
            shared.append("".join(t.text or "" for t in si.iter(M + "t")))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).find(M + "sheets"):
        target = rels[sheet.get(R + "id")].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(target)).iter(M + "row"):
            cells = {}
            for c in row.findall(M + "c"):
                col = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group(0):
                    col = col * 26 + ord(ch) - 64
                v = c.find(M + "v")
                if c.get("t") == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(M + "t"))
                elif v is None:
                    continue
                elif c.get("t") == "s":
                    val = shared[int(v.text)]
                else:
                    val = v.text
                cells[col - 1] = val.strip()
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        yield sheet.get("name").strip(), rows


def parse_sheet(rows):
    title = rows[0][1] if rows and len(rows[0]) > 1 else ""
    title = re.sub(r"^ΤΥΠΟΣ ΠΑΡΑΣΤΑΤΙΚΟΥ:\s*", "", title).strip()
    text = " ".join(" ".join(r) for r in rows)
    # Greek and Latin "E" are mixed in some headers; normalise before matching.
    has_expense_side = "ΞΟΔΩΝ" in text.replace("E", "Ε")
    entry = {"title": title}
    if not has_expense_side:
        entry["expenses"] = None
        entry["note"] = "AADE lists no expense classifications for this invoice type."
        return entry
    if "συσχετιζόμενου παραστατικού" in text:
        entry["expenses"] = "correlated"
        entry["note"] = "Allowed values are those of the correlated (original) invoice type."
        return entry
    cats, current = {}, None
    for r in rows[4:]:
        cat = r[4] if len(r) > 4 else ""
        val = r[5] if len(r) > 5 else ""
        if cat.startswith("category2_"):
            current = cat
            cats.setdefault(current, [])
        if not current or not val:
            continue
        if re.fullmatch(r"(E3|VAT|NOT_VAT)_[0-9_]+", val):
            if isinstance(cats[current], list) and val not in cats[current]:
                cats[current].append(val)
        elif "επιτρεπτοί οι παραπάνω" in val:
            cats[current] = "any-above"
        elif "Δεν ενημερώνει" in val or "Δεν γίνεται χαρακτηρισμός" in val:
            cats[current] = "no-e3"
    if not cats:
        entry["expenses"] = None
        entry["note"] = "AADE lists no expense classifications for this invoice type."
        return entry
    entry["expenses"] = cats
    subtables = [r[1] for r in rows[5:] if len(r) > 1 and r[1] and not r[1].startswith("category")
                 and "/" in r[1] and "επιτρεπτοί" not in r[1] and "μεταφορά" not in r[1]]
    if subtables:
        entry["note"] = "Sheet has extra sub-tables merged here: " + "; ".join(subtables)
    return entry


def main():
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "aade-mydata-skills"})
        data = urllib.request.urlopen(req, timeout=60).read()
    types = {name: parse_sheet(rows) for name, rows in read_sheets(data)}
    doc = {
        "_source": {
            "title": "AADE myDATA - Συνδυασμοί χαρακτηρισμών (characterization combinations)",
            "url": SOURCE_URL,
            "page": SOURCE_PAGE,
            "specVersion": SPEC_VERSION,
            "extracted": date.today().isoformat(),
            "legend": {
                "list": "allowed classificationType values for that category",
                "any-above": "any E3 value allowed above for this invoice type (prior/next fiscal year)",
                "no-e3": "category does not update E3; send no classificationType",
                "correlated": "use the combinations of the correlated invoice type",
                "null": "no expense classifications defined for this invoice type",
            },
            "scope": "Expense side, E3 checks only. VAT_ codes are not part of this spreadsheet.",
        },
        "invoiceTypes": types,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(types)} invoice types)")


if __name__ == "__main__":
    main()
