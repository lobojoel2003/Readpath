# SPDX-License-Identifier: Apache-2.0
"""Optional second opinion from veraPDF, the open-source PDF/UA validator.

veraPDF runs as a separate program (it is GPL-3.0 / MPL-2.0 licensed Java
software). Set READPATH_VERAPDF to its launcher, or put `verapdf` on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROFILES = {"ua1": "PDF/UA-1", "ua2": "PDF/UA-2", "wt1a": "WTPDF 1.0 Accessibility"}


@dataclass
class VeraResult:
    available: bool
    ran: bool = False
    compliant: bool | None = None
    profile: str | None = None
    failed_rules: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def verapdf_command() -> str | None:
    return os.environ.get("READPATH_VERAPDF") or shutil.which("verapdf")


def available() -> bool:
    return verapdf_command() is not None


def validate(path: str | Path, flavour: str = "ua1", timeout: int = 300) -> VeraResult:
    cmd = verapdf_command()
    if not cmd:
        return VeraResult(available=False)
    try:
        proc = subprocess.run([cmd, "--flavour", flavour, "--format", "json", str(path)],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return VeraResult(True, False, error="veraPDF took too long on this file.")
    except OSError as exc:
        return VeraResult(True, False, error=f"Could not run veraPDF: {exc}")
    out = proc.stdout
    start = out.find("{")
    if start < 0:
        return VeraResult(True, False, error=(proc.stderr or "veraPDF produced no report").strip()[:300])
    try:
        data = json.loads(out[start:])
        job = data["report"]["jobs"][0]
        vr = job.get("validationResult")
        if isinstance(vr, list):
            vr = vr[0] if vr else None
        if vr is None:
            err = job.get("taskException") or job.get("taskResult") or {}
            return VeraResult(True, False, error=str(err)[:300] or "veraPDF could not validate this file.")
        details = vr.get("details", {})
        rules = []
        for r in details.get("ruleSummaries", []):
            if r.get("ruleStatus", r.get("status", "")).lower() in ("failed", "fail"):
                rules.append({
                    "clause": str(r.get("clause", "")),
                    "test": int(r.get("testNumber", 0) or 0),
                    "description": str(r.get("description", "")).strip(),
                    "count": int(r.get("failedChecks", 0) or 0),
                })
        return VeraResult(True, True, bool(vr.get("compliant")), PROFILES.get(flavour, flavour), rules)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        return VeraResult(True, False, error=f"Could not read veraPDF's report ({exc}).")
