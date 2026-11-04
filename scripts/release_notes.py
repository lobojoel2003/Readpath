#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print the CHANGELOG.md section for a version (used for GitHub Releases).

    python scripts/release_notes.py v0.1.0 > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes(version: str) -> str:
    version = version.removeprefix("refs/tags/").removeprefix("v")
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    body = m.group(1)
    # Drop link reference definitions at the end of the file
    body = re.sub(r"^\[[^\]]+\]: \S+\s*$", "", body, flags=re.M)
    return body.strip() + "\n"


if __name__ == "__main__":
    print(notes(sys.argv[1] if len(sys.argv) > 1 else ""), end="")
