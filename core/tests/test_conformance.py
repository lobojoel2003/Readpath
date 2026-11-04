# SPDX-License-Identifier: Apache-2.0
"""PDF/UA-1 conformance of Readpath's output, checked with veraPDF.

Skipped unless veraPDF is installed (READPATH_VERAPDF or `verapdf` on PATH).
CI installs it, so every change is checked against the reference validator.
"""

import os

import pikepdf
import pytest

import samples
from readpath_core import autofix_file, embed_fonts, finalize_pdf, verapdf
from readpath_core.review import Review

if os.environ.get("READPATH_REQUIRE_VERAPDF") and not (verapdf.available() and embed_fonts.available()):
    raise RuntimeError("READPATH_REQUIRE_VERAPDF is set but veraPDF or the Liberation fonts are missing")

pytestmark = pytest.mark.skipif(not verapdf.available(), reason="veraPDF not installed")

# Rules that need a person (alt text) or are added when the review finishes (PDF/UA mark)
NEEDS_PERSON = {("7.3", 1), ("5", 1)}


def failed(path) -> set[tuple[str, int]]:
    result = verapdf.validate(path)
    assert result.ran, result.error
    return {(r["clause"], r["test"]) for r in result.failed_rules}


@pytest.mark.parametrize("make", ["council_minutes", "browser_style"])
def test_autofix_output_passes_except_human_items(tmp_path, make):
    src = getattr(samples, make)(tmp_path / "src.pdf")
    out = tmp_path / "fixed.pdf"
    autofix_file(src, out, ocr=False)
    remaining = failed(out)
    if not embed_fonts.available():
        remaining.discard(("7.21.4.1", 1))
    assert remaining <= NEEDS_PERSON, remaining


def test_page_drawn_as_form_xobject_passes(tmp_path):
    src = samples.form_page(samples.council_minutes(tmp_path / "c.pdf"), tmp_path / "form.pdf")
    out = tmp_path / "fixed.pdf"
    autofix_file(src, out, ocr=False)
    remaining = failed(out)
    if not embed_fonts.available():
        remaining.discard(("7.21.4.1", 1))
    assert remaining <= NEEDS_PERSON, remaining


@pytest.mark.skipif(not embed_fonts.available(), reason="Liberation fonts not installed")
def test_reviewed_file_is_fully_compliant(tmp_path):
    src = samples.council_minutes(tmp_path / "src.pdf")
    fixed = tmp_path / "fixed.pdf"
    autofix_file(src, fixed, ocr=False)
    with pikepdf.open(fixed, allow_overwriting_input=True) as pdf:
        r = Review(pdf)
        r.set_figure(r.figures()[0]["id"], alt="Bar chart of road repair spending by quarter.")
        r.set_table_headers(r.tables()[0]["id"], header_row=True, header_column=True)
        r.set_metadata("Regular Council Meeting Minutes", "en-US")
        for step in ("basics", "reading-order", "images", "tables"):
            r.complete_step(step)
        assert finalize_pdf(pdf)
        pdf.save(fixed)
    result = verapdf.validate(fixed)
    assert result.compliant, result.failed_rules
