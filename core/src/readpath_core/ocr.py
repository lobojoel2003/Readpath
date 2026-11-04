# SPDX-License-Identifier: Apache-2.0
"""Optional text recognition for scanned PDFs, using OCRmyPDF if installed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# BCP 47 language -> Tesseract language pack
_TESSERACT = {
    "en": "eng", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "it": "ita", "vi": "vie",
    "zh": "chi_sim", "ko": "kor", "ja": "jpn", "ru": "rus", "ar": "ara", "tl": "tgl", "ht": "hat",
    "pl": "pol", "nl": "nld", "fa": "fas", "hi": "hin",
}


def ocr_command() -> str | None:
    return os.environ.get("READPATH_OCRMYPDF") or shutil.which("ocrmypdf")


def ocr_available() -> bool:
    if os.environ.get("READPATH_DISABLE_OCR"):
        return False
    return ocr_command() is not None


def run_ocr(src: str | Path, dst: str | Path, lang: str | None = None, timeout: int = 900) -> None:
    """Add an invisible text layer to scanned pages. Pages with text are left alone."""
    cmd = ocr_command()
    if not cmd:
        raise RuntimeError("OCR is not installed on this server.")
    base = (lang or "en").split("-")[0].lower()
    tess = _TESSERACT.get(base, "eng")
    args = [cmd, "--skip-text", "--output-type", "pdf", "--optimize", "0", "--quiet", "-l", tess,
            str(src), str(dst)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode not in (0, 6):  # 6 = already has text
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else f"ocrmypdf exited with {proc.returncode}")
    if proc.returncode == 6:
        shutil.copyfile(src, dst)
