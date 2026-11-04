# SPDX-License-Identifier: Apache-2.0

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import ImageChops

import samples
from readpath_core import autofix_file, check_file, cli
from readpath_core import metadata as md
from readpath_core.checks import ProblemOpening
from readpath_core.content import analyze_page, to_view_fraction
from readpath_core.fonts import CMap
from readpath_core.lang import detect_language, is_valid_lang
from readpath_core.layout import list_start
from readpath_core.review import Review, ReviewError
from readpath_core.structure import blocks_in_order, read_tree
from readpath_core.tagger import autotag, is_tagged


@pytest.fixture()
def council(tmp_path):
    return samples.council_minutes(tmp_path / "council.pdf")


@pytest.fixture()
def fixed(tmp_path, council):
    out = tmp_path / "fixed.pdf"
    autofix_file(council, out, ocr=False)
    return out


def check(report, cid):
    return next(c for c in report.checks if c.id == cid)


# --- low-level pieces -------------------------------------------------------

def test_cmap_parsing():
    cmap = CMap.parse(b"""
        1 begincodespacerange <0000> <FFFF> endcodespacerange
        2 beginbfchar <0003> <0020> <0011> <0041> endbfchar
        1 beginbfrange <0020> <0022> <0061> endbfrange
        1 beginbfrange <0030> <0031> [<0066006C> <0078>] endbfrange
    """)
    assert cmap.to_unicode[b"\x00\x03"] == " "
    assert cmap.to_unicode[b"\x00\x11"] == "A"
    assert cmap.to_unicode[b"\x00\x21"] == "b"
    assert cmap.to_unicode[b"\x00\x30"] == "fl"
    assert cmap.split_codes(b"\x00\x11\x00\x03", 2) == [b"\x00\x11", b"\x00\x03"]


def test_language_detection():
    assert detect_language("The council will meet on Tuesday and the public is invited to attend the hearing "
                           "at the library.")[0] == "en-US"
    assert detect_language("El concejo municipal se reunirá el martes y el público está invitado a la "
                           "audiencia en la biblioteca de la ciudad.")[0] == "es"
    assert detect_language("short")[0] is None
    assert is_valid_lang("en-US") and is_valid_lang("zh-Hant") and not is_valid_lang("english")


def test_list_detection():
    assert list_start("• Item") == "Disc"
    assert list_start("1. First") == "Decimal"
    assert list_start("a) option") == "LowerAlpha"
    assert list_start("-5 degrees") is None
    assert list_start("Budget") is None


def test_view_fraction_rotation():
    box = (0, 0, 100, 200)
    assert to_view_fraction((0, 150, 50, 200), box, 0) == (0.0, 0.0, 0.5, 0.25)
    # rotated 90 degrees clockwise: original top-left corner moves to top-right
    assert to_view_fraction((0, 150, 50, 200), box, 90) == (0.75, 0.0, 1.0, 0.5)


def test_content_walker_positions(council):
    with pikepdf.open(council) as pdf:
        pc = analyze_page(pdf.pages[0])
    texts = [it for it in pc.items if it.kind == "text" and it.text.strip()]
    title = texts[0]
    assert title.text == "Regular Council Meeting Minutes"
    assert title.bold and round(title.size) == 22
    assert 690 < title.bbox[1] < 705
    assert any(it.text == "•" for it in texts)  # WinAnsi bullet
    assert any(it.kind == "image" for it in pc.items)


# --- checking ---------------------------------------------------------------

def test_untagged_report(council):
    report = check_file(council)
    assert not report.tagged
    assert report.grade == "auto_fixable"
    assert report.score < 50
    assert check(report, "tagged").status == "fail"
    assert check(report, "tagged").fix == "auto"
    assert check(report, "alt-text").count == 1
    assert check(report, "language").status == "fail"
    assert check(report, "title").status == "warning"  # reportlab's "untitled"


def test_scanned_report(tmp_path):
    report = check_file(samples.scanned_page(tmp_path / "scan.pdf"))
    assert check(report, "text").status == "fail"
    assert check(report, "alt-text").status == "not_applicable"  # OCR, not alt text, is the fix


def test_unreadable_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"<html>not a pdf</html>")
    with pytest.raises(ProblemOpening):
        check_file(bad)


# --- tagging and fixing -----------------------------------------------------

def test_autotag_structure(council):
    with pikepdf.open(council) as pdf:
        result = autotag(pdf)
        assert result.tagged and is_tagged(pdf)
        tree = read_tree(pdf)
        types = [b.std for b in blocks_in_order(tree)]
    assert types[:5] == ["H1", "H2", "P", "H2", "L"]
    assert "Figure" in types and "Table" in types
    assert result.links == 1
    std = {e.std for e in tree.elements}
    assert {"TH", "TD", "TR", "LI", "Lbl", "LBody", "Link"} <= std


def _changed_fraction(a_path, b_path) -> list[float]:
    a, b = pdfium.PdfDocument(str(a_path)), pdfium.PdfDocument(str(b_path))
    out = []
    for i in range(len(a)):
        ia = a[i].render(scale=2).to_pil().convert("L")
        ib = b[i].render(scale=2).to_pil().convert("L")
        hist = ImageChops.difference(ia, ib).histogram()
        out.append(sum(hist[64:]) / (ia.size[0] * ia.size[1]))
    return out


def test_tagging_keeps_appearance(tmp_path, council):
    out = tmp_path / "tagged-only.pdf"
    autofix_file(council, out, ocr=False, embed_fonts=False)
    assert _changed_fraction(council, out) == [0.0, 0.0, 0.0]


def test_autofix_keeps_appearance(council, fixed):
    # Embedded substitute fonts have the same widths; only glyph outlines differ slightly
    assert max(_changed_fraction(council, fixed)) < 0.01


def test_embeds_standard_fonts(council, fixed):
    from readpath_core import embed_fonts

    if not embed_fonts.available():
        pytest.skip("Liberation fonts not installed")
    assert check(check_file(council), "fonts-embedded").fix == "auto"
    assert check(check_file(fixed), "fonts-embedded").status == "pass"


def test_autofix_improves_score(council, fixed):
    before, after = check_file(council), check_file(fixed)
    assert after.score > before.score + 25
    assert after.grade == "needs_help"
    assert check(after, "tagged").status == "pass"
    assert check(after, "untagged-content").status == "pass"
    assert check(after, "language").status == "pass"
    assert check(after, "links").status == "pass"
    assert check(after, "reading-order").status == "warning"
    assert check(after, "table-headers").status == "warning"
    with pikepdf.open(fixed) as pdf:
        assert md.get_title(pdf) == "Regular Council Meeting Minutes"
        assert md.title_guessed(pdf) and md.autotagged(pdf)
        assert pdf.pages[0].obj.Tabs == pikepdf.Name.S


def test_autofix_is_idempotent(tmp_path, fixed):
    again = tmp_path / "again.pdf"
    result = autofix_file(fixed, again, ocr=False)
    assert not any("Added tags" in line for line in result.applied)
    assert check_file(again).score == check_file(fixed).score


# --- guided review ----------------------------------------------------------

def test_review_flow(tmp_path, fixed):
    with pikepdf.open(fixed, allow_overwriting_input=True) as pdf:
        r = Review(pdf)
        blocks = r.page_blocks(0)
        assert blocks[0]["type"] == "H1" and blocks[0]["bbox"][1] < 0.15
        ids = [b["id"] for b in blocks]
        r.reorder_page(0, list(reversed(ids)))
        assert [b["text"] for b in r.page_blocks(0)] == [b["text"] for b in reversed(blocks)]
        r.reorder_page(0, [b["id"] for b in reversed(r.page_blocks(0))])
        para = next(b for b in r.page_blocks(0) if b["text"].startswith("Full agenda"))
        r.set_block_type(para["id"], "H3")
        assert any(b["type"] == "H3" for b in r.page_blocks(0))
        with pytest.raises(ReviewError):
            r.set_block_type(para["id"], "Table")

        fig = r.figures()[0]
        r.set_figure(fig["id"], alt="Bar chart: road repair spending rose each quarter.")
        assert r.figures()[0]["alt"].startswith("Bar chart")

        table = r.tables()[0]
        r.set_table_headers(table["id"], header_row=True, header_column=True)
        t = r.tables()[0]
        assert t["header_row"] and t["header_column"]
        r.set_metadata("Council Minutes, March 2026", "en-US")
        for step in ("basics", "reading-order", "images", "tables"):
            r.complete_step(step)
        pdf.save(tmp_path / "reviewed.pdf")
    report = check_file(tmp_path / "reviewed.pdf")
    failing = {c.id for c in report.checks if c.failing}
    assert not failing, failing
    assert report.grade == "ready"
    assert report.score >= 95


def test_decorative_figure_and_artifact(tmp_path, fixed):
    with pikepdf.open(fixed) as pdf:
        r = Review(pdf)
        r.set_figure(r.figures()[0]["id"], decorative=True)
        assert r.figures() == []
        first = r.page_blocks(0)[0]
        r.set_block_type(first["id"], "Artifact")
        assert r.page_blocks(0)[0]["id"] != first["id"] or r.page_blocks(0)[0]["text"] != first["text"]
        pdf.save(tmp_path / "out.pdf")
    report = check_file(tmp_path / "out.pdf")
    assert check(report, "untagged-content").status == "pass"
    assert check(report, "alt-text").status == "not_applicable"


def test_review_rejects_bad_input(fixed):
    with pikepdf.open(fixed) as pdf:
        r = Review(pdf)
        with pytest.raises(ReviewError):
            r.reorder_page(0, ["e1"])
        with pytest.raises(ReviewError):
            r.set_metadata(None, "english")
        with pytest.raises(ReviewError):
            r.element("e99999")
        with pytest.raises(ReviewError):
            r.set_figure(r.figures()[0]["id"], alt="   ")


def test_cli(tmp_path, council, capsys):
    out = tmp_path / "cli.pdf"
    assert cli.main(["fix", str(council), "-o", str(out), "--no-ocr"]) == 0
    assert cli.main(["check", str(out)]) == 1  # still needs a person
    printed = capsys.readouterr().out
    assert "Images have descriptions" in printed


def test_vector_bullets_and_unruled_table(tmp_path):
    src = samples.browser_style(tmp_path / "browser.pdf")
    with pikepdf.open(src) as pdf:
        result = autotag(pdf)
        tree = read_tree(pdf)
    types = [b.std for b in blocks_in_order(tree)]
    assert types == ["H1", "L", "Figure", "Table"], types  # the drawing is a figure
    assert result.lists == 1 and result.tables == 1 and result.figures == 1
    lbls = [e for e in tree.elements if e.std == "Lbl"]
    assert len(lbls) == 3  # the drawn circles are the list labels
    rows = [e for e in tree.elements if e.std == "TR"]
    assert len(rows) == 4


def test_two_column_reading_order(council):
    with pikepdf.open(council) as pdf:
        autotag(pdf)
        r = Review(pdf)
        texts = [b["text"] for b in r.page_blocks(2)]
    assert texts[0].startswith("Public Comment")
    assert texts[1].startswith("First column") and texts[2].startswith("Second column")


def test_page_drawn_as_form_xobject(tmp_path, council):
    src = samples.form_page(council, tmp_path / "form.pdf")
    out = tmp_path / "form-fixed.pdf"
    autofix_file(src, out, ocr=False)
    with pikepdf.open(out) as pdf:
        types = [b.std for b in blocks_in_order(read_tree(pdf))]
    assert types[:5] == ["H1", "H2", "P", "H2", "L"]
    assert max(_changed_fraction(src, out)) < 0.01


def test_rotated_page_boxes(tmp_path, council):
    with pikepdf.open(council) as pdf:
        pdf.pages[0].obj.Rotate = 90
        autotag(pdf)
        r = Review(pdf)
        assert r.page_sizes()[0]["width"] == 792
        title = r.page_blocks(0)[0]
    left, top, right, bottom = title["bbox"]
    # The title runs down the right-hand edge of a page turned clockwise
    assert left > 0.85 and bottom - top > right - left
