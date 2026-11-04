#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke-test a running Readpath server, for example the Docker image.

    python core/tests/samples.py /tmp/samples        # make test PDFs (needs reportlab)
    python scripts/smoke_test.py http://localhost:8000 /tmp/samples --expect-ocr --expect-verapdf --expect-fonts

Uploads sample PDFs, runs the automatic fixes and checks the results through
the HTTP API. Uses only the standard library. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        FAILURES.append(what)


def call(base: str, method: str, path: str, body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=600) as resp:
        content = resp.read()
        return (content, resp.headers) if raw else json.loads(content)


def upload(base: str, files: list[Path], name: str) -> dict:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for f in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode() + f.read_bytes() + b"\r\n")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{name}\r\n'.encode())
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(base + "/api/uploads", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def wait_for(fn, timeout: float, what: str):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    raise SystemExit(f"Timed out waiting for {what} (last: {last})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="server URL, e.g. http://localhost:8000")
    ap.add_argument("samples", type=Path, help="folder with council.pdf and scanned.pdf")
    ap.add_argument("--expect-ocr", action="store_true", help="OCR must be installed and work")
    ap.add_argument("--expect-verapdf", action="store_true", help="veraPDF must be installed and run")
    ap.add_argument("--expect-fonts", action="store_true", help="font substitutes must be installed")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("Health")
    health = wait_for(lambda: call(base, "GET", "/api/health"), 120, "the server to start")
    check(health["status"] == "ok", f"server is up ({health['version']})")
    if args.expect_verapdf:
        check(health["verapdf"], "veraPDF is available")
    if args.expect_ocr:
        check(health["ocr"], "OCR is available")

    print("Web app")
    html, _ = call(base, "GET", "/", raw=True)
    check(b"<title>Readpath" in html, "web app is served at /")
    html, _ = call(base, "GET", "/scans/some-id", raw=True)
    check(b'id="root"' in html, "client-side routes fall back to the app")

    print("Upload and check")
    scan = upload(base, [args.samples / "council.pdf", args.samples / "scanned.pdf"], "Smoke test")
    scan = wait_for(lambda: (s := call(base, "GET", f"/api/scans/{scan['id']}"))["status"] in ("done", "failed") and s,
                    300, "the upload to be checked")
    check(scan["status"] == "done", f"scan finished: {scan['message']}")
    docs = {d["filename"]: d for d in call(base, "GET", f"/api/scans/{scan['id']}/documents")}
    council, scanned = docs["council.pdf"], docs["scanned.pdf"]
    check(council["status"] == "checked" and council["grade"] == "auto_fixable",
          f"council.pdf checked: score {council['score']}, {council['grade']}")

    print("Automatic fixes")
    report = call(base, "POST", f"/api/documents/{council['id']}/autofix")
    after = report["document"]["score"]
    check(after >= council["score"] + 25, f"council.pdf score {council['score']} -> {after}")
    fixes = " ".join(report["fixes_applied"])
    check("Added tags" in fixes, "tags were added")
    if args.expect_fonts:
        check("Embedded" in fixes, "standard fonts were embedded")
    page, headers = call(base, "GET", f"/api/documents/{council['id']}/pages/1/image?width=400", raw=True)
    check(page[:8] == b"\x89PNG\r\n\x1a\n", "page image renders")

    if args.expect_verapdf:
        print("veraPDF")
        report = call(base, "POST", f"/api/documents/{council['id']}/recheck")
        vera = report["verapdf"] or {}
        rules = {f"{r['clause']}-{r['test']}" for r in vera.get("failed_rules", [])}
        check(bool(vera.get("ran")), "veraPDF ran on the fixed file")
        allowed = {"7.3-1", "5-1"} | (set() if args.expect_fonts else {"7.21.4.1-1"})
        check(rules <= allowed, f"only items for a person remain (failed: {sorted(rules) or 'none'})")

    if args.expect_ocr:
        print("OCR")
        report = call(base, "POST", f"/api/documents/{scanned['id']}/autofix")
        checks = {c["id"]: c for c in report["checks"]}
        check(any("OCR" in f for f in report["fixes_applied"]), "text recognition ran on the scan")
        check(checks["text"]["status"] == "pass", f"scan now has real text ({checks['text']['message']})")
        check(checks["tagged"]["status"] == "pass", "scan was tagged after OCR")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("All smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
