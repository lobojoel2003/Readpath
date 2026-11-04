# SPDX-License-Identifier: Apache-2.0
"""Command line: ``readpath check FILE`` and ``readpath fix FILE -o OUT``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, verapdf
from .checks import ProblemOpening, check_file
from .fixer import autofix_file

_ICON = {"pass": "ok  ", "fail": "FAIL", "warning": "warn", "manual": "look", "not_applicable": " -  "}


def _print_report(path: str, report) -> None:
    print(f"{path}: score {report.score}/100 ({report.grade.replace('_', ' ')}), {report.page_count} pages")
    for c in report.checks:
        if c.status == "not_applicable":
            continue
        fix = f"  [fix: {c.fix}]" if c.fix and c.status != "pass" else ""
        refs = ", ".join([f"WCAG {w}" for w in c.wcag] + [f"PDF/UA {p}" for p in c.pdfua])
        print(f"  {_ICON[c.status]} {c.title} - {c.message}{fix}")
        if c.status in ("fail", "warning") and refs:
            print(f"       {refs}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="readpath", description="Check and fix PDF accessibility.")
    parser.add_argument("--version", action="version", version=f"readpath-core {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="check one or more PDFs")
    p_check.add_argument("files", nargs="+")
    p_check.add_argument("--json", action="store_true", help="print JSON")
    p_check.add_argument("--verapdf", action="store_true", help="also run veraPDF (PDF/UA-1)")

    p_fix = sub.add_parser("fix", help="apply safe automatic fixes")
    p_fix.add_argument("file")
    p_fix.add_argument("-o", "--output", required=True)
    p_fix.add_argument("--lang", default="en-US", help="language to use if it can't be detected")
    p_fix.add_argument("--retag", action="store_true", help="replace existing tags")
    p_fix.add_argument("--no-ocr", action="store_true", help="never run OCR")

    args = parser.parse_args(argv)
    if args.command == "check":
        results = []
        status = 0
        for f in args.files:
            try:
                report = check_file(f)
            except ProblemOpening as exc:
                print(f"{f}: {exc}", file=sys.stderr)
                status = 2
                continue
            data = report.to_dict()
            if args.verapdf:
                data["verapdf"] = verapdf.validate(f).to_dict()
            if args.json:
                results.append({"file": f, **data})
            else:
                _print_report(f, report)
                if args.verapdf:
                    v = data["verapdf"]
                    if not v["available"]:
                        print("  veraPDF is not installed")
                    elif v["ran"]:
                        print(f"  veraPDF {v['profile']}: {'compliant' if v['compliant'] else 'not compliant'}")
                        for r in v["failed_rules"]:
                            print(f"    {r['clause']}-{r['test']} ({r['count']}x): {r['description']}")
            if report.grade != "ready":
                status = max(status, 1)
        if args.json:
            print(json.dumps(results, indent=2))
        return status

    result = autofix_file(args.file, args.output, default_lang=args.lang, ocr=False if args.no_ocr else None,
                          retag=args.retag)
    for line in result.applied:
        print(f"fixed: {line}")
    for line in result.skipped:
        print(f"skipped: {line}")
    report = check_file(args.output)
    print(f"{Path(args.output).name}: score {report.score}/100 ({report.grade.replace('_', ' ')})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
