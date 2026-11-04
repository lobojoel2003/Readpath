# SPDX-License-Identifier: Apache-2.0
"""Safe automatic fixes.

"Safe" means the fix never changes how the document looks and never removes
anything a person wrote. Guesses (title, reading order, table headers) are
flagged so the checker asks a person to confirm them.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name

from . import metadata as md
from .checks import DocumentFacts
from .content import page_box
from .embed_fonts import embed_standard_fonts
from .lang import detect_language, is_valid_lang
from .ocr import ocr_available, run_ocr
from .review import Review
from .structure import Kid, blocks_in_order, read_tree
from .tagger import autotag, is_tagged, link_description


@dataclass
class FixResult:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    tagged_now: bool = False


def remove_tags(pdf: pikepdf.Pdf) -> None:
    """Drop an existing (broken) structure tree so the file can be re-tagged."""
    if "/StructTreeRoot" in pdf.Root:
        del pdf.Root["/StructTreeRoot"]
    for page in pdf.pages:
        if "/StructParents" in page.obj:
            del page.obj["/StructParents"]
        for a in page.obj.get("/Annots", []) or []:
            if isinstance(a, Dictionary) and "/StructParent" in a:
                del a["/StructParent"]


def _scanned_pages(facts: DocumentFacts) -> int:
    return sum(1 for i in range(facts.analyzed) if facts.text_chars[i] < 10 and facts.image_cover[i] > 0.4)


def autofix_pdf(pdf: pikepdf.Pdf, *, filename: str | None = None, default_lang: str = "en-US",
                retag: bool = False, embed_fonts: bool = True) -> FixResult:
    """Apply safe fixes to an open PDF in place."""
    result = FixResult()
    facts = DocumentFacts(pdf)
    has_text = any(c >= 10 for c in facts.text_chars)

    # Tags
    if retag and is_tagged(pdf):
        remove_tags(pdf)
        result.applied.append("Removed the old, incomplete tags so the file could be re-tagged.")
    if not is_tagged(pdf):
        if has_text:
            tag = autotag(pdf, force=True)
            md.mark_autotagged(pdf)
            result.tagged_now = True
            parts = []
            if tag.headings:
                parts.append(f"{tag.headings} heading{'s' if tag.headings != 1 else ''}")
            if tag.lists:
                parts.append(f"{tag.lists} list{'s' if tag.lists != 1 else ''}")
            if tag.tables:
                parts.append(f"{tag.tables} table{'s' if tag.tables != 1 else ''}")
            if tag.figures:
                parts.append(f"{tag.figures} image{'s' if tag.figures != 1 else ''}")
            if tag.links:
                parts.append(f"{tag.links} link{'s' if tag.links != 1 else ''}")
            result.applied.append(
                "Added tags so screen readers can follow the document"
                + (f" (found {', '.join(parts)})" if parts else "") + "."
            )
        else:
            result.skipped.append("The file has no real text, so it can't be tagged until text recognition "
                                  "(OCR) is run.")

    # Language
    lang = md.get_lang(pdf)
    if not lang or not is_valid_lang(lang):
        detected, confidence = detect_language(facts.text)
        if detected and confidence >= 0.2:
            # Prefer the configured regional variant (e.g. en-US) when the base language matches
            same = detected.split("-")[0] == default_lang.split("-")[0]
            new_lang = default_lang if same else detected
        else:
            new_lang = default_lang
        md.set_lang(pdf, new_lang)
        result.applied.append(f"Set the document language to {new_lang}.")

    # Title
    title = md.get_title(pdf)
    if not md.is_meaningful_title(title):
        review = Review(pdf)
        guess = review.suggested_title(filename)
        if guess and md.is_meaningful_title(guess):
            md.set_title(pdf, guess, guessed=True)
            result.applied.append(f"Set the title to “{guess}” (please confirm it).")
        elif title:
            md.set_title(pdf, title, guessed=True)
    elif md.xmp_title(pdf) is None:
        md.set_title(pdf, title, guessed=md.title_guessed(pdf))  # copy into XMP metadata
    if md.get_title(pdf) and not md.display_title(pdf):
        md.set_display_title(pdf)
        result.applied.append("Told PDF readers to show the title instead of the file name.")

    if is_tagged(pdf):
        mark = pdf.Root.get("/MarkInfo")
        if mark is None or not mark.get("/Marked", False):
            pdf.Root.MarkInfo = Dictionary(Marked=True)
        n = _fix_annotations(pdf)
        if n:
            result.applied.append(f"Tagged {n} link{'s' if n != 1 else ''} or form field{'s' if n != 1 else ''} "
                                  "that screen readers were missing.")

    # Fonts: embed metric-compatible substitutes for the standard fonts
    embedded = embed_standard_fonts(pdf) if embed_fonts else 0
    if embedded:
        result.applied.append(f"Embedded {embedded} font{'s' if embedded != 1 else ''} (exact-width "
                              "substitutes for the standard PDF fonts).")

    # Link descriptions
    described = 0
    for page in pdf.pages:
        for a in page.obj.get("/Annots", []) or []:
            if isinstance(a, Dictionary) and a.get("/Subtype") == Name.Link and not str(a.get("/Contents", "")).strip():
                a.Contents = pikepdf.String(link_description(a))
                described += 1
    if described:
        result.applied.append(f"Added descriptions to {described} link{'s' if described != 1 else ''}.")

    # Tab order
    fixed_tabs = 0
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is not None and len(annots) and page.obj.get("/Tabs") != Name.S:
            page.obj.Tabs = Name.S
            fixed_tabs += 1
    if fixed_tabs:
        result.applied.append("Made the Tab key follow the reading order.")

    # Bookmarks
    if len(pdf.pages) >= 10 and is_tagged(pdf):
        outlines = pdf.Root.get("/Outlines")
        if outlines is None or outlines.get("/First") is None:
            made = _bookmarks_from_headings(pdf)
            if made:
                result.applied.append(f"Created {made} bookmarks from the headings.")
    return result


def _fix_annotations(pdf: pikepdf.Pdf) -> int:
    """Tag annotations that a tagged file left out of its structure tree."""
    tree = read_tree(pdf)
    if tree.root is None or not tree.top:
        return 0
    tagged: set[tuple[int, int]] = set()
    for el in tree.elements:
        for k in el.kids:
            if isinstance(k, Kid) and k.kind == "objr" and k.obj is not None and k.obj.is_indirect:
                tagged.add(k.obj.objgen)
    root = tree.root
    if "/ParentTree" not in root:
        root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array()))
    nt = pikepdf.NumberTree(root.ParentTree)
    next_key = int(root.get("/ParentTreeNextKey", 0) or 0)
    existing = list(nt.keys())
    if existing:
        next_key = max(next_key, max(existing) + 1)
    review = Review(pdf)
    blocks = blocks_in_order(tree)
    doc = tree.top[0]
    added = 0
    for i, page in enumerate(pdf.pages):
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        for a in annots:
            if not isinstance(a, Dictionary) or not a.is_indirect or a.objgen in tagged:
                continue
            sub = a.get("/Subtype")
            if sub in (Name.Popup, Name.PrinterMark, Name.TrapNet) or int(a.get("/F", 0) or 0) & 2:
                continue
            kind = "Link" if sub == Name.Link else ("Form" if sub == Name.Widget else "Annot")
            host = doc.obj
            if kind == "Link":
                try:
                    r = [float(v) for v in a.Rect]
                    rect = (min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3]))
                except Exception:  # noqa: BLE001
                    rect = None
                best = 0.0
                for b in blocks:
                    if rect is None or b.std in ("Figure", "Table", "L", "Form"):
                        continue
                    if i not in review.pages(b):
                        continue
                    bb = review.elem_bbox(b, i)
                    if bb is None:
                        continue
                    ox = min(bb[2], rect[2]) - max(bb[0], rect[0])
                    oy = min(bb[3], rect[3]) - max(bb[1], rect[1])
                    if ox > 0 and oy > 0 and ox * oy > best:
                        best, host = ox * oy, b.obj
            se = pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name("/" + kind), P=host, Pg=page.obj,
                K=Dictionary(Type=Name.OBJR, Obj=a, Pg=page.obj)))
            arr = host.get("/K")
            if not isinstance(arr, Array):
                host.K = Array([] if arr is None else [arr])
            host.K.append(se)
            a.StructParent = next_key
            nt[next_key] = se
            next_key += 1
            added += 1
    root.ParentTreeNextKey = next_key
    return added


def _bookmarks_from_headings(pdf: pikepdf.Pdf) -> int:
    review = Review(pdf)
    heads = [e for e in review.tree.elements if e.std in ("H1", "H2", "H3", "H")]
    if not heads:
        return 0
    made = 0
    with pdf.open_outline() as outline:
        if outline.root:
            return 0
        stack: list[tuple[int, pikepdf.OutlineItem]] = []
        for el in heads[:500]:
            pages = review.pages(el)
            if not pages:
                continue
            title = review.elem_text(el, 200)
            if not title:
                continue
            level = 1 if el.std in ("H", "H1") else int(el.std[1])
            box = review.elem_bbox(el, pages[0])
            top = box[3] + 4 if box else page_box(pdf.pages[pages[0]])[3]
            item = pikepdf.OutlineItem(title[:120], pages[0], "XYZ", top=top)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].children.append(item)
            else:
                outline.root.append(item)
            stack.append((level, item))
            made += 1
    return made


def autofix_file(src: str | Path, dst: str | Path, *, filename: str | None = None,
                 default_lang: str = "en-US", ocr: bool | None = None, retag: bool = False,
                 embed_fonts: bool = True) -> FixResult:
    """Fix a file and write the result to `dst` (which may equal `src`)."""
    src, dst = Path(src), Path(dst)
    filename = filename or src.name
    tmpdir = Path(tempfile.mkdtemp(prefix="readpath-"))
    try:
        work = tmpdir / "work.pdf"
        shutil.copyfile(src, work)
        notes: list[str] = []
        with pikepdf.open(work) as probe:
            facts = DocumentFacts(probe)
            scanned = _scanned_pages(facts)
            blocked = probe.is_encrypted and not (
                getattr(probe.allow, "accessibility", True) or getattr(probe.allow, "extract", True))
        use_ocr = ocr if ocr is not None else ocr_available()
        if scanned and use_ocr:
            ocred = tmpdir / "ocr.pdf"
            try:
                run_ocr(work, ocred, md_lang_hint(src))
                work = ocred
                notes.append(f"Recognized text on {scanned} scanned page{'s' if scanned != 1 else ''} (OCR).")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Text recognition (OCR) failed: {exc}")
        with pikepdf.open(work) as pdf:
            result = autofix_pdf(pdf, filename=filename, default_lang=default_lang, retag=retag,
                                 embed_fonts=embed_fonts)
            result.applied = notes + result.applied
            if blocked:
                result.applied.append("Removed a security setting that blocked screen readers.")
            out = tmpdir / "out.pdf"
            pdf.save(out, encryption=False if blocked else None)
        shutil.move(str(out), dst)
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def md_lang_hint(path: Path) -> str | None:
    try:
        with pikepdf.open(path) as pdf:
            return md.get_lang(pdf)
    except Exception:  # noqa: BLE001
        return None


# Checks allowed to remain when declaring PDF/UA: advisory items only. An
# unconfirmed reading order that Readpath guessed shows as a warning and blocks.
_FINAL_OK = {"pdfua"}


def finalize_pdf(pdf: pikepdf.Pdf) -> bool:
    """Declare PDF/UA conformance if every automatic check passes.

    Called when a person finishes the guided review. Returns True if the
    PDF/UA identifier was added (or was already there)."""
    from .checks import run_checks

    report = run_checks(pdf)
    blocking = [c for c in report.checks if c.failing and c.id not in _FINAL_OK]
    if blocking:
        return False
    if not md.pdfua_marked(pdf):
        md.set_pdfua(pdf)
    return True
