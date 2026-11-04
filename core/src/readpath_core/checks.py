# SPDX-License-Identifier: Apache-2.0
"""Accessibility checks for PDF files, with a 0-100 score.

Each check maps to WCAG 2.1 success criteria and, where relevant, PDF/UA-1
(ISO 14289-1) clauses. Messages are written for people who are not
accessibility specialists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Name

from . import metadata as md
from .content import PageContent, analyze_page, page_box
from .fonts import FontCache
from .lang import is_valid_lang
from .structure import StructTree, read_tree
from .tagger import is_tagged

SEVERITY_WEIGHT = {"critical": 10, "serious": 6, "moderate": 3, "minor": 1}


@dataclass
class Check:
    id: str
    title: str
    status: str  # pass | fail | warning | manual | not_applicable
    severity: str  # critical | serious | moderate | minor
    wcag: list[str]
    pdfua: list[str]
    message: str
    help: str
    fix: str | None = None  # auto | guided | manual
    step: str | None = None  # basics | reading-order | images | tables
    count: int | None = None
    total: int | None = None
    details: list[str] = field(default_factory=list)

    @property
    def failing(self) -> bool:
        return self.status in ("fail", "warning")


@dataclass
class Report:
    checks: list[Check]
    score: int
    grade: str  # ready | auto_fixable | needs_help
    page_count: int
    title: str | None
    lang: str | None
    tagged: bool
    stats: dict

    def to_dict(self) -> dict:
        return {
            "checks": [asdict(c) for c in self.checks],
            "score": self.score,
            "grade": self.grade,
            "page_count": self.page_count,
            "title": self.title,
            "lang": self.lang,
            "tagged": self.tagged,
            "stats": self.stats,
        }

    def counts(self) -> dict:
        failing = [c for c in self.checks if c.failing]
        return {
            "failed": len(failing),
            "auto_fixable": sum(1 for c in failing if c.fix == "auto"),
            "needs_help": sum(1 for c in failing if c.fix in ("guided", "manual")),
            "passed": sum(1 for c in self.checks if c.status == "pass"),
        }


class ProblemOpening(Exception):
    """The file could not be opened as a PDF."""


def _pages_text(pages: list[int]) -> str:
    nums = [p + 1 for p in pages]
    if len(nums) > 12:
        return "pages " + ", ".join(map(str, nums[:12])) + f" and {len(nums) - 12} more"
    return ("page " if len(nums) == 1 else "pages ") + ", ".join(map(str, nums))


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


class DocumentFacts:
    """Everything the checks need, gathered in one pass over the file."""

    def __init__(self, pdf: pikepdf.Pdf, max_pages: int | None = None):
        self.pdf = pdf
        self.fonts = FontCache()
        self.page_count = len(pdf.pages)
        limit = self.page_count if max_pages is None else min(max_pages, self.page_count)
        self.analyzed = limit
        self.contents: list[PageContent] = []
        self.text_chars: list[int] = []
        self.image_cover: list[float] = []
        self.figure_candidates = 0
        self.text_sample: list[str] = []
        for i in range(limit):
            page = pdf.pages[i]
            pc = analyze_page(page, self.fonts)
            self.contents.append(pc)
            box = page_box(page)
            area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
            chars = 0
            cover = 0.0
            page_figures = 0
            for it in pc.items:
                if it.kind == "text":
                    chars += len(it.text.strip())
                    if len(self.text_sample) < 4000 and it.text.strip():
                        self.text_sample.append(it.text)
                elif it.kind == "form" and it.form_text:
                    chars += len(it.text.strip())
                    if len(self.text_sample) < 4000:
                        self.text_sample.append(it.text)
                if it.kind in ("image", "inline_image") or (it.kind == "form" and it.form_image):
                    w = max(0.0, min(it.bbox[2], box[2]) - max(it.bbox[0], box[0]))
                    h = max(0.0, min(it.bbox[3], box[3]) - max(it.bbox[1], box[1]))
                    cover = max(cover, w * h / area)
                    if w >= 12 and h >= 12 and w * h >= 300 and not it.artifact:
                        page_figures += 1
            self.text_chars.append(chars)
            self.image_cover.append(cover)
            if not (chars < 10 and cover > 0.4):  # images on scanned pages need OCR, not alt text
                self.figure_candidates += page_figures
        self.tagged = is_tagged(pdf)
        self.tree: StructTree = read_tree(pdf) if self.tagged else StructTree(None, [], [], {}, set())
        self.page_index = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}

    @property
    def text(self) -> str:
        return " ".join(self.text_sample)


def _annotations(pdf: pikepdf.Pdf):
    for i, page in enumerate(pdf.pages):
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        for a in annots:
            if isinstance(a, pikepdf.Dictionary):
                yield i, page, a


def _field_tooltip(widget: pikepdf.Object) -> str | None:
    node = widget
    for _ in range(10):
        if node is None:
            break
        if "/TU" in node and str(node.TU).strip():
            return str(node.TU)
        node = node.get("/Parent")
    return None


def run_checks(pdf: pikepdf.Pdf, *, filename: str | None = None, ocr_available: bool = False,
               max_pages: int | None = None) -> Report:
    facts = DocumentFacts(pdf, max_pages=max_pages)
    checks: list[Check] = []
    n = facts.page_count
    analyzed = range(facts.analyzed)
    tree = facts.tree
    autotagged = md.autotagged(pdf)
    reviewed = md.reviewed_steps(pdf)

    # 1. Real text (not scanned images)
    scanned = [i for i in analyzed if facts.text_chars[i] < 10 and facts.image_cover[i] > 0.4]
    blank = [i for i in analyzed if facts.text_chars[i] < 10 and facts.image_cover[i] <= 0.4]
    has_text = any(c >= 10 for c in facts.text_chars)
    ocr_fix = "auto" if ocr_available else "manual"
    if scanned and len(scanned) == facts.analyzed:
        checks.append(Check(
            "text", "Text can be read (not just a picture)", "fail", "critical", ["1.1.1", "1.4.5"], ["7.1"],
            "This file is a scan: every page is a picture of text, so screen readers find nothing to read.",
            "Text recognition (OCR) turns the picture into real, selectable text. "
            + ("Readpath can run OCR for you." if ocr_available else
               "Run OCR in your scanner software or Acrobat, or upload the original document instead."),
            ocr_fix, None, len(scanned), n, [_pages_text(scanned)]))
    elif scanned:
        checks.append(Check(
            "text", "Text can be read (not just a picture)", "warning", "serious", ["1.1.1", "1.4.5"], ["7.1"],
            f"{_pages_text(scanned).capitalize()} look scanned: the text there is a picture.",
            "Scanned pages need text recognition (OCR) so screen readers can read them. "
            + ("Readpath can run OCR for you." if ocr_available else "Run OCR on those pages, or replace them."),
            ocr_fix, None, len(scanned), n, []))
    elif not has_text:
        checks.append(Check(
            "text", "Text can be read (not just a picture)", "not_applicable", "critical", ["1.1.1"], ["7.1"],
            "This file has no text.", "Nothing to read here.", None))
    else:
        checks.append(Check(
            "text", "Text can be read (not just a picture)", "pass", "critical", ["1.1.1", "1.4.5"], ["7.1"],
            "The file contains real text.", "Screen readers can read real text.", None))

    # 2. Tagged
    if facts.tagged:
        checks.append(Check(
            "tagged", "Document has tags", "pass", "critical", ["1.3.1", "4.1.2"], ["7.1"],
            "The file has tags (hidden structure that screen readers follow)."
            + (" Readpath added them." if autotagged else ""),
            "Tags tell assistive technology what is a heading, paragraph, list, table or image.", None))
    else:
        fix = "auto" if has_text else ocr_fix
        checks.append(Check(
            "tagged", "Document has tags", "fail", "critical", ["1.3.1", "4.1.2"], ["7.1"],
            "This file has no tags, so screen readers read it as one long, unstructured stream "
            "(or not at all).",
            "Tags are hidden structure that marks headings, paragraphs, lists, tables and images. "
            + ("Readpath can add them automatically; you then check the result in the guided review."
               if has_text else "The file needs text recognition (OCR) before it can be tagged."),
            fix, None))

    # 3. Untagged content inside a tagged file
    if facts.tagged:
        referenced: set[tuple[tuple[int, int], int]] = set()
        for el in tree.elements:
            for k in el.kids:
                if getattr(k, "kind", None) == "mcid" and k.page is not None and k.stm is None:
                    referenced.add((k.page.objgen, k.mcid))
        bad_pages = []
        bad_items = 0
        total_items = 0
        for i in analyzed:
            pc = facts.contents[i]
            page_gen = pdf.pages[i].obj.objgen
            orphans = sum(1 for m in pc.mcids if (page_gen, m) not in referenced)
            count = pc.unmarked + orphans
            total_items += sum(1 for it in pc.items if it.kind in ("text", "image", "inline_image", "form"))
            if count:
                bad_pages.append(i)
                bad_items += count
        if bad_pages:
            mostly = total_items and bad_items > 0.5 * total_items
            checks.append(Check(
                "untagged-content", "All content is tagged", "fail", "serious", ["1.3.1"], ["7.1"],
                f"Some text or images on {_pages_text(bad_pages)} are not in the tags, so screen readers "
                "skip them.",
                "Every piece of content should be tagged, or marked as decoration. "
                + ("Most of this file is untagged, so Readpath can re-tag it from scratch."
                   if mostly else "Re-export the file from its source with tagging turned on, or fix the "
                   "tags in Acrobat."),
                "auto" if mostly else "manual", None, bad_items, None, []))
        else:
            checks.append(Check(
                "untagged-content", "All content is tagged", "pass", "serious", ["1.3.1"], ["7.1"],
                "All content is tagged or marked as decoration.", "Nothing is hidden from screen readers.",
                None))

    # 4. Title
    title = md.get_title(pdf)
    suggested = None
    if not title:
        checks.append(Check(
            "title", "Document has a title", "fail", "serious", ["2.4.2"], ["7.1"],
            "The file has no title. Screen readers announce the file name instead.",
            "A title like “Council Meeting Minutes, March 2026” tells people what the document is. "
            "Readpath suggests one from the first heading, which you can confirm in the review.",
            "auto", "basics"))
    elif not md.is_meaningful_title(title):
        checks.append(Check(
            "title", "Document has a title", "warning", "moderate", ["2.4.2"], ["7.1"],
            f"The title “{title}” doesn’t describe the document.",
            "Replace it with a short description of what the document is.", "guided", "basics"))
    elif md.title_guessed(pdf) and "basics" not in reviewed:
        checks.append(Check(
            "title", "Document has a title", "warning", "moderate", ["2.4.2"], ["7.1"],
            f"Readpath set the title to “{title}”. Please confirm it.",
            "Check the suggested title in the guided review.", "guided", "basics"))
    else:
        checks.append(Check(
            "title", "Document has a title", "pass", "serious", ["2.4.2"], ["7.1"],
            f"Title: “{title}”.", "The title tells people what the document is.", None))

    # 5. Show title instead of file name
    xmp_ok = md.xmp_title(pdf) is not None
    if title and md.display_title(pdf) and xmp_ok:
        checks.append(Check(
            "display-title", "Title is shown instead of the file name", "pass", "moderate", ["2.4.2"],
            ["7.1"], "PDF readers show the title in the window bar.", "", None))
    else:
        checks.append(Check(
            "display-title", "Title is shown instead of the file name", "fail", "moderate", ["2.4.2"],
            ["7.1"], "PDF readers show the file name in the window bar instead of the title.",
            "A small setting tells PDF readers to show the title. Readpath can turn it on.", "auto"))

    # 6. Language
    lang = md.get_lang(pdf)
    if lang and is_valid_lang(lang):
        checks.append(Check(
            "language", "Document language is set", "pass", "serious", ["3.1.1"], ["7.2"],
            f"Language: {lang}.", "Screen readers use it to pronounce words correctly.", None))
    else:
        checks.append(Check(
            "language", "Document language is set", "fail", "serious", ["3.1.1"], ["7.2"],
            "The document language is not set" + (f" (“{lang}” is not a valid code)" if lang else "")
            + ", so screen readers may mispronounce everything.",
            "Readpath detects the language from the text; you can change it in the review.", "auto", "basics"))

    # 7. Images have alt text
    figures = [e for e in tree.elements if e.std == "Figure"]
    if facts.tagged:
        missing = [e for e in figures if not ((e.alt or "").strip() or (e.actual_text or "").strip())]
        if not figures:
            checks.append(Check(
                "alt-text", "Images have descriptions", "not_applicable", "critical", ["1.1.1"], ["7.3"],
                "No tagged images.", "", None, "images", 0, 0))
        elif missing:
            checks.append(Check(
                "alt-text", "Images have descriptions", "fail", "critical", ["1.1.1"], ["7.3"],
                f"{_plural(len(missing), 'image')} of {len(figures)} {'has' if len(missing) == 1 else 'have'} "
                "no description (alt text).",
                "Describe what each image shows or why it is there, in a sentence or two. Mark purely "
                "decorative images so screen readers skip them.", "guided", "images", len(missing),
                len(figures)))
        else:
            checks.append(Check(
                "alt-text", "Images have descriptions", "pass", "critical", ["1.1.1"], ["7.3"],
                "The image has a description." if len(figures) == 1 else
                f"All {len(figures)} images have descriptions.", "", None, "images", 0, len(figures)))
    elif facts.figure_candidates:
        checks.append(Check(
            "alt-text", "Images have descriptions", "fail", "critical", ["1.1.1"], ["7.3"],
            f"This file has {_plural(facts.figure_candidates, 'image')} with no description.",
            "After Readpath adds tags, the guided review walks you through describing each image.",
            "guided", "images", facts.figure_candidates, facts.figure_candidates))
    else:
        checks.append(Check(
            "alt-text", "Images have descriptions", "not_applicable", "critical", ["1.1.1"], ["7.3"],
            "No images found.", "", None, "images", 0, 0))

    # 8. Tables have headers
    tables = [e for e in tree.elements if e.std == "Table"]
    if facts.tagged and tables:
        no_headers = [t for t in tables if not any(x.std == "TH" for x in t.walk())]
        if no_headers:
            checks.append(Check(
                "table-headers", "Tables have header cells", "fail", "serious", ["1.3.1"], ["7.5"],
                f"{_plural(len(no_headers), 'table')} of {len(tables)} {'has' if len(no_headers) == 1 else 'have'} "
                "no header cells, so screen readers can't say which column or row a value belongs to.",
                "Mark the row or column that holds the headings. The guided review shows each table.",
                "guided", "tables", len(no_headers), len(tables)))
        elif autotagged and "tables" not in reviewed:
            checks.append(Check(
                "table-headers", "Tables have header cells", "warning", "serious", ["1.3.1"], ["7.5"],
                f"Readpath guessed the headers for {_plural(len(tables), 'table')}. Please confirm them.",
                "Check that the right row or column is marked as headings.", "guided", "tables",
                len(tables), len(tables)))
        else:
            checks.append(Check(
                "table-headers", "Tables have header cells", "pass", "serious", ["1.3.1"], ["7.5"],
                "The table has header cells." if len(tables) == 1 else
                f"All {len(tables)} tables have header cells.", "", None, "tables", 0, len(tables)))
    else:
        checks.append(Check(
            "table-headers", "Tables have header cells", "not_applicable", "serious", ["1.3.1"], ["7.5"],
            "No tagged tables." if facts.tagged else "Checked after tags are added.", "", None, "tables",
            0, 0))

    # 9. Headings
    headings = [e for e in tree.elements if e.std in ("H", "H1", "H2", "H3", "H4", "H5", "H6")]
    if facts.tagged:
        levels = [int(e.std[1]) for e in headings if len(e.std) == 2]
        skips = []
        prev = 0
        for lv in levels:
            if lv > prev + 1:
                skips.append(f"Heading {lv} follows heading {prev}" if prev else f"First heading is level {lv}")
            prev = lv
        if not headings and n >= 2:
            checks.append(Check(
                "headings", "Headings organize the document", "warning", "moderate", ["1.3.1", "2.4.6"],
                ["7.4"], "No headings are tagged, so people can't jump between sections.",
                "Mark section titles as headings in the reading-order step.", "guided", "reading-order"))
        elif skips:
            checks.append(Check(
                "headings", "Headings organize the document", "warning", "moderate", ["1.3.1", "2.4.6"],
                ["7.4.2"], "Heading levels skip a step (for example, heading 1 straight to heading 3).",
                "Use levels in order: heading 1 for the title, heading 2 for sections, and so on.",
                "guided", "reading-order", len(skips), len(headings), skips[:5]))
        else:
            checks.append(Check(
                "headings", "Headings organize the document",
                "pass" if headings else "not_applicable", "moderate", ["1.3.1", "2.4.6"], ["7.4"],
                f"{_plural(len(headings), 'heading')}, in a logical order." if headings
                else "Short document without headings.", "", None))
    else:
        checks.append(Check(
            "headings", "Headings organize the document", "not_applicable", "moderate", ["1.3.1", "2.4.6"],
            ["7.4"], "Checked after tags are added.", "", None))

    # 10. Reading order
    if facts.tagged:
        if "reading-order" in reviewed:
            checks.append(Check(
                "reading-order", "Reading order makes sense", "pass", "moderate", ["1.3.2", "2.4.3"], ["7.2"],
                "A person checked the reading order.", "", None, "reading-order"))
        elif autotagged:
            checks.append(Check(
                "reading-order", "Reading order makes sense", "warning", "moderate", ["1.3.2", "2.4.3"],
                ["7.2"], "Readpath worked out the reading order from the page layout. Please check it.",
                "The guided review shows each page with numbered blocks so you can fix the order and mark "
                "headings.", "guided", "reading-order"))
        else:
            checks.append(Check(
                "reading-order", "Reading order makes sense", "manual", "moderate", ["1.3.2", "2.4.3"],
                ["7.2"], "Software can't fully judge reading order. A quick look is recommended.",
                "Open the guided review to see the order screen readers will use.", "guided", "reading-order"))
    else:
        checks.append(Check(
            "reading-order", "Reading order makes sense", "not_applicable", "moderate", ["1.3.2"], ["7.2"],
            "Checked after tags are added.", "", None, "reading-order"))

    # 11/12. Fonts
    not_embedded: set[str] = set()
    no_unicode: set[str] = set()
    for pc in facts.contents:
        for info in pc.fonts_used.values():
            clean = info.base_name.split("+", 1)[-1]
            if not info.embedded and info.subtype != "/Type3":
                not_embedded.add(clean)
            if not info.unicode_ok:
                no_unicode.add(clean)
    if no_unicode:
        checks.append(Check(
            "fonts-unicode", "Text uses real characters", "fail", "serious", ["1.3.1", "4.1.2"], ["7.21.7"],
            f"Text in {_plural(len(no_unicode), 'font')} can't be turned into real characters, so screen "
            "readers may read nonsense.",
            "This usually comes from old or unusual fonts. Re-export the document from its original "
            "file (Word, InDesign, etc.) or run OCR.", "manual", None, len(no_unicode), None,
            sorted(no_unicode)[:10]))
    else:
        checks.append(Check(
            "fonts-unicode", "Text uses real characters", "pass" if has_text else "not_applicable", "serious",
            ["1.3.1"], ["7.21.7"], "All text maps to real characters.", "", None))
    if not_embedded:
        checks.append(Check(
            "fonts-embedded", "Fonts are embedded", "warning", "minor", [], ["7.21.4.1"],
            f"{_plural(len(not_embedded), 'font')} {'is' if len(not_embedded) == 1 else 'are'} not embedded.",
            "PDF/UA requires embedded fonts so text displays the same everywhere. Re-export with "
            "“embed fonts” turned on.", "manual", None, len(not_embedded), None,
            sorted(not_embedded)[:10]))
    else:
        checks.append(Check(
            "fonts-embedded", "Fonts are embedded", "pass" if has_text else "not_applicable", "minor", [],
            ["7.21.4.1"], "All fonts are embedded.", "", None))

    # 13. Links, 14. Forms, 15. Tab order, other annotations
    objr_targets: set[tuple[int, int]] = set()
    link_elem_targets: set[tuple[int, int]] = set()
    for el in tree.elements:
        for k in el.kids:
            if getattr(k, "kind", None) == "objr" and k.obj is not None and k.obj.is_indirect:
                objr_targets.add(k.obj.objgen)
                if el.std == "Link":
                    link_elem_targets.add(k.obj.objgen)
    links = undescribed = untagged_links = 0
    widgets = unlabeled = 0
    untagged_annots = 0
    tab_pages = []
    for i, page, a in _annotations(pdf):
        sub = a.get("/Subtype")
        if sub in (Name.Popup, Name.PrinterMark, Name.TrapNet):
            continue
        if page.obj.get("/Tabs") != Name.S and i not in tab_pages:
            tab_pages.append(i)
        tagged_annot = a.is_indirect and a.objgen in objr_targets
        if sub == Name.Link:
            links += 1
            if not str(a.get("/Contents", "")).strip():
                undescribed += 1
            if facts.tagged and not (a.is_indirect and a.objgen in link_elem_targets):
                untagged_links += 1
        elif sub == Name.Widget:
            widgets += 1
            if not _field_tooltip(a):
                unlabeled += 1
            if facts.tagged and not tagged_annot:
                untagged_annots += 1
        elif facts.tagged and not tagged_annot and not (int(a.get("/F", 0) or 0) & 2):
            untagged_annots += 1
    if links:
        problems = []
        if undescribed:
            problems.append(f"{_plural(undescribed, 'link')} {'has' if undescribed == 1 else 'have'} no description")
        if untagged_links or not facts.tagged:
            problems.append("links are not tagged")
        checks.append(Check(
            "links", "Links are tagged and described", "fail" if problems else "pass", "moderate",
            ["2.4.4", "4.1.2"], ["7.18.1", "7.18.5"],
            ("; ".join(problems).capitalize() + ".") if problems else (
                "The link is tagged and described." if links == 1 else f"All {links} links are tagged and described."),
            "Screen readers need each link tagged with a short description of where it goes. Readpath "
            "can add these.", "auto" if problems else None, None, (undescribed or untagged_links) or None, links))
    if widgets:
        checks.append(Check(
            "forms", "Form fields have labels", "fail" if unlabeled else "pass", "serious",
            ["1.3.1", "3.3.2", "4.1.2"], ["7.18.1", "7.18.4"],
            f"{_plural(unlabeled, 'form field')} of {widgets} {'has' if unlabeled == 1 else 'have'} no label "
            "(tooltip)." if unlabeled else ("The form field has a label." if widgets == 1 else
                                            f"All {widgets} form fields have labels."),
            "Each fillable field needs a tooltip that says what to enter. Add them in Acrobat's form "
            "editor (Properties → Tooltip).", "manual" if unlabeled else None, None, unlabeled or None,
            widgets))
    if facts.tagged and untagged_annots:
        checks.append(Check(
            "annotations", "Comments and form fields are tagged", "fail", "minor", ["4.1.2"], ["7.18.1"],
            f"{_plural(untagged_annots, 'annotation')} {'is' if untagged_annots == 1 else 'are'} not in the tags.",
            "Readpath can add them to the tags.", "auto", None, untagged_annots))
    if tab_pages:
        checks.append(Check(
            "tab-order", "Tab key follows the reading order", "fail", "minor", ["2.4.3"], ["7.18.3"],
            f"On {_pages_text(tab_pages)}, pressing Tab may jump around the page.",
            "A page setting makes the Tab key follow the tags. Readpath can turn it on.", "auto", None,
            len(tab_pages)))

    # 16. Bookmarks for long documents
    if n >= 10:
        outlines = pdf.Root.get("/Outlines")
        has_outline = outlines is not None and outlines.get("/First") is not None
        heading_count = len(headings)
        if has_outline:
            checks.append(Check(
                "bookmarks", "Long document has bookmarks", "pass", "moderate", ["2.4.5"], [],
                "Bookmarks help people jump to sections.", "", None))
        else:
            can = heading_count > 0 or not facts.tagged
            checks.append(Check(
                "bookmarks", "Long document has bookmarks", "warning", "moderate", ["2.4.5"], [],
                f"This {n}-page document has no bookmarks.",
                "Bookmarks let people jump between sections. "
                + ("Readpath can create them from the headings." if can else "Mark headings first."),
                "auto" if can else "guided", None if can else "reading-order"))

    # 17. Security
    if pdf.is_encrypted:
        allow = pdf.allow
        blocked = not (getattr(allow, "accessibility", True) or getattr(allow, "extract", True))
        checks.append(Check(
            "security", "Security settings allow screen readers", "fail" if blocked else "pass", "critical",
            ["4.1.2"], ["7.16"],
            "Security settings block screen readers from reading the text." if blocked
            else "The file is protected, but screen readers are allowed in.",
            "Readpath can remove the restriction from your copy." if blocked else "",
            "auto" if blocked else None))

    # 18. Structure types
    if facts.tagged and tree.unknown_types:
        checks.append(Check(
            "structure-types", "Tags use standard names", "warning", "minor", ["4.1.2"], ["7.1"],
            f"{_plural(len(tree.unknown_types), 'tag type')} {'is' if len(tree.unknown_types) == 1 else 'are'} "
            "not standard and not mapped.",
            "Custom tags need a role map so screen readers know what they mean.", "manual", None,
            len(tree.unknown_types), None, sorted(tree.unknown_types)[:10]))

    # 19. PDF/UA identifier (advisory)
    if md.pdfua_marked(pdf):
        checks.append(Check(
            "pdfua", "Marked as PDF/UA", "pass", "minor", [], ["5"],
            "The file declares PDF/UA conformance.", "", None))
    else:
        checks.append(Check(
            "pdfua", "Marked as PDF/UA", "manual", "minor", [], ["5"],
            "The file doesn't declare PDF/UA conformance yet.",
            "Readpath adds the PDF/UA mark when you finish the guided review and nothing automatic fails.",
            "guided"))

    score = compute_score(checks)
    grade = compute_grade(checks)
    stats = {
        "pages": n,
        "pages_analyzed": facts.analyzed,
        "images": len(figures) if facts.tagged else facts.figure_candidates,
        "tables": len(tables),
        "headings": len(headings),
        "links": links,
        "form_fields": widgets,
        "scanned_pages": len(scanned),
        "blank_pages": len(blank),
        "autotagged": autotagged,
        "reviewed": sorted(reviewed),
        "suggested_title": suggested,
    }
    order = {"fail": 0, "warning": 1, "manual": 2, "pass": 3, "not_applicable": 4}
    sev = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    checks.sort(key=lambda c: (order[c.status], sev[c.severity]))
    return Report(checks, score, grade, n, title, lang if lang else None, facts.tagged, stats)


def compute_score(checks: list[Check]) -> int:
    possible = earned = 0.0
    for c in checks:
        if c.status not in ("pass", "fail", "warning"):
            continue
        w = SEVERITY_WEIGHT[c.severity]
        possible += w
        if c.status == "pass":
            earned += w
        elif c.status == "warning":
            earned += w * 0.5
        elif c.count is not None and c.total:
            earned += w * max(0.0, (c.total - c.count) / c.total) * 0.8
    if possible == 0:
        return 100
    return int(round(100 * earned / possible))


def compute_grade(checks: list[Check]) -> str:
    relevant = [c for c in checks if c.failing and c.severity != "minor"]
    if any(c.fix == "auto" for c in relevant):
        return "auto_fixable"
    if relevant:
        return "needs_help"
    return "ready"


def check_file(path: str | Path, **kwargs) -> Report:
    """Open and check a PDF file. Raises ProblemOpening for unreadable files."""
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError as exc:
        raise ProblemOpening("This PDF is password-protected. Upload a copy without a password.") from exc
    except Exception as exc:  # noqa: BLE001
        raise ProblemOpening("This file could not be opened as a PDF. It may be damaged.") from exc
    with pdf:
        kwargs.setdefault("filename", Path(path).name)
        return run_checks(pdf, **kwargs)
