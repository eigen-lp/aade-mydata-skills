#!/usr/bin/env python3
"""Build the plugin zip for Customize > Plugins > Upload plugin (claude.ai / Cowork).

    python3 tools/package_plugin.py            # -> dist/aade-mydata-<version>.zip
    python3 tools/package_plugin.py --out x.zip

The zip holds exactly one .claude-plugin/plugin.json plus the skill, licence and
READMEs. Tests, tools, credentials and OS clutter are left out. Entries are sorted
and timestamped from a fixed date, so the same sources give the same bytes.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = [".claude-plugin/plugin.json", "skills", "LICENSE", "README.md", "README.el.md", "SECURITY.md"]
SKIP_NAMES = {".DS_Store", "__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def plugin_files(root=ROOT):
    files = []
    for entry in INCLUDE:
        path = root / entry
        candidates = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for p in candidates:
            rel = p.relative_to(root)
            if any(part in SKIP_NAMES or part.startswith(".env") for part in rel.parts):
                continue
            if p.suffix in SKIP_SUFFIXES:
                continue
            files.append(rel)
    return sorted(set(files), key=lambda r: r.as_posix())


def build(out=None, root=ROOT):
    version = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    out = Path(out) if out else root / "dist" / f"aade-mydata-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in plugin_files(root):
            info = zipfile.ZipInfo(rel.as_posix(), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel.suffix == ".py" else 0o644) << 16
            zf.writestr(info, (root / rel).read_bytes())
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", help="zip path (default: dist/aade-mydata-<version>.zip)")
    out = build(p.parse_args().out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    with zipfile.ZipFile(out) as zf:
        count = len(zf.namelist())
    print(f"wrote {out} ({count} files, {out.stat().st_size} bytes)\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
