#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that every package declares the same version.

    python scripts/check_version.py            # all versions agree
    python scripts/check_version.py v0.1.0     # ...and match this tag
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions() -> dict[str, str]:
    found = {
        "core/pyproject.toml": tomllib.loads((ROOT / "core/pyproject.toml").read_text())["project"]["version"],
        "server/pyproject.toml": tomllib.loads((ROOT / "server/pyproject.toml").read_text())["project"]["version"],
        "web/package.json": json.loads((ROOT / "web/package.json").read_text())["version"],
    }
    for path in ("core/src/readpath_core/__init__.py", "server/src/readpath_server/__init__.py"):
        m = re.search(r'^__version__ = "([^"]+)"', (ROOT / path).read_text(), re.M)
        found[path] = m.group(1) if m else "missing"
    return found


def main() -> int:
    found = versions()
    expected = sys.argv[1].removeprefix("refs/tags/").removeprefix("v") if len(sys.argv) > 1 else None
    distinct = set(found.values())
    for path, version in found.items():
        print(f"{version:>10}  {path}")
    if len(distinct) != 1:
        print("Versions disagree.", file=sys.stderr)
        return 1
    if expected is not None and expected not in distinct:
        print(f"Tag says {expected} but the packages say {distinct.pop()}.", file=sys.stderr)
        return 1
    print(f"All packages are at {distinct.pop()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
