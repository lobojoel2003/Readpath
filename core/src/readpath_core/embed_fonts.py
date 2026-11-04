# SPDX-License-Identifier: Apache-2.0
"""Embed metric-compatible fonts for the standard PDF fonts.

Many generated PDFs use Helvetica, Times or Courier without embedding them,
which PDF/UA forbids (7.21.4.1). The Liberation fonts have exactly the same
character widths as Arial/Helvetica, Times New Roman/Times and Courier New,
so embedding them keeps every character in the same place. A font is only
replaced if every width matches the original to within one unit; otherwise it
is left alone.

Needs fontTools and the Liberation fonts (Debian: fonts-liberation2). Set
READPATH_FONT_DIR to point at a folder containing the Liberation .ttf files.
"""

from __future__ import annotations

import io
import os
import random
import string
from functools import lru_cache
from pathlib import Path

import pikepdf
from pdfminer.fontmetrics import FONT_METRICS
from pikepdf import Array, Dictionary, Name

from .fonts import _BASE14_ALIASES, _STD, _WIN, FontInfo, glyph_to_unicode

_SEARCH = [
    "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/liberation",
    "/usr/share/fonts/TTF",
    "/Library/Fonts",
]

# Standard font -> (Liberation family, style)
_FAMILIES = {
    "Helvetica": ("Sans", "Regular"), "Helvetica-Bold": ("Sans", "Bold"),
    "Helvetica-Oblique": ("Sans", "Italic"), "Helvetica-BoldOblique": ("Sans", "BoldItalic"),
    "Times-Roman": ("Serif", "Regular"), "Times-Bold": ("Serif", "Bold"),
    "Times-Italic": ("Serif", "Italic"), "Times-BoldItalic": ("Serif", "BoldItalic"),
    "Courier": ("Mono", "Regular"), "Courier-Bold": ("Mono", "Bold"),
    "Courier-Oblique": ("Mono", "Italic"), "Courier-BoldOblique": ("Mono", "BoldItalic"),
}


def _font_dirs() -> list[Path]:
    dirs = [Path(os.environ["READPATH_FONT_DIR"])] if os.environ.get("READPATH_FONT_DIR") else []
    return dirs + [Path(p) for p in _SEARCH]


@lru_cache(maxsize=16)
def _font_file(family: str, style: str) -> Path | None:
    name = f"Liberation{family}-{style}.ttf"
    for d in _font_dirs():
        p = d / name
        if p.is_file():
            return p
    return None


def available() -> bool:
    try:
        import fontTools  # noqa: F401
    except ImportError:
        return False
    return _font_file("Sans", "Regular") is not None


def _standard_name(base: str) -> str | None:
    clean = base.split("+", 1)[-1]
    clean = _BASE14_ALIASES.get(clean, clean)
    return clean if clean in _FAMILIES else None


def _subset_tag() -> str:
    return "".join(random.choice(string.ascii_uppercase) for _ in range(6))


def _used_codes(pdf: pikepdf.Pdf) -> dict[tuple[int, int], set[int]]:
    """Character codes drawn with each simple font (by font object id)."""
    used: dict[tuple[int, int], set[int]] = {}
    seen_forms: set[tuple[int, int]] = set()

    def walk(stream_owner, resources, depth: int) -> None:
        if depth > 8:
            return
        try:
            ops = pikepdf.parse_content_stream(stream_owner)
        except Exception:  # noqa: BLE001
            return
        fonts = resources.get("/Font") if resources is not None else None
        xobjects = resources.get("/XObject") if resources is not None else None
        current = None
        stack: list = []
        for instr in ops:
            try:
                op = str(instr.operator)
            except Exception:  # noqa: BLE001
                continue
            if op == "q":
                stack.append(current)
            elif op == "Q":
                current = stack.pop() if stack else None
            elif op == "Tf" and instr.operands and fonts is not None:
                f = fonts.get(str(instr.operands[0]))
                current = f.objgen if f is not None and f.is_indirect else None
            elif op in ("Tj", "'", '"', "TJ") and current is not None and instr.operands:
                codes = used.setdefault(current, set())
                arg = instr.operands[-1]
                parts = list(arg) if op == "TJ" else [arg]
                for part in parts:
                    if isinstance(part, pikepdf.String):
                        codes.update(bytes(part))
            elif op == "Do" and instr.operands and xobjects is not None:
                x = xobjects.get(str(instr.operands[0]))
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form and x.objgen not in seen_forms:
                    seen_forms.add(x.objgen)
                    walk(x, x.get("/Resources", resources), depth + 1)

    for page in pdf.pages:
        walk(page, page.obj.get("/Resources"), 0)
    return used


def _embed_one(pdf: pikepdf.Pdf, font: pikepdf.Object, used: set[int] | None = None) -> bool:
    import logging

    from fontTools import subset
    from fontTools.ttLib import TTFont

    if font.get("/Subtype") not in (Name.Type1, Name.TrueType):
        return False
    desc = font.get("/FontDescriptor")
    if desc is not None and any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")):
        return False
    std = _standard_name(str(font.get("/BaseFont", ""))[1:])
    if std is None:
        return False
    path = _font_file(*_FAMILIES[std])
    if path is None:
        return False

    info = FontInfo(font)
    names = dict(info._simple_names)  # code -> glyph name, as the viewer would use
    if not names:
        names = dict(_STD if font.get("/Subtype") == Name.Type1 else _WIN)
    tt = TTFont(str(path))
    cmap = tt.getBestCmap()
    upem = tt["head"].unitsPerEm
    hmtx = tt["hmtx"]
    _, afm = FONT_METRICS[std]

    widths: dict[int, int] = {}
    chars: set[int] = set()
    for code in range(32, 256):
        name = names.get(code)
        if not name:
            continue
        text = glyph_to_unicode(name)
        if len(text) != 1 or ord(text) not in cmap:
            continue
        glyph = cmap[ord(text)]
        width = round(hmtx[glyph][0] * 1000 / upem)
        drawn = used is None or code in used
        expected = afm.get(text)
        if drawn and expected is not None and abs(expected - width) > 1:
            return False  # not metric-compatible for a character in use; leave the font alone
        if drawn and "/Widths" in font:
            first = int(font.get("/FirstChar", 0))
            try:
                declared = float(font.Widths[code - first])
                if 0 <= code - first and abs(declared - width) > 1:
                    return False
            except (IndexError, TypeError, ValueError):
                pass
        widths[code] = width
        chars.add(ord(text))
    if not widths:
        return False

    # Encoding: WinAnsi plus Differences for codes whose glyph differs
    diffs = Array()
    last = None
    for code in sorted(widths):
        name = names.get(code)
        if _WIN.get(code) != name:
            if last is None or code != last + 1:
                diffs.append(code)
            diffs.append(Name("/" + name))
            last = code
    encoding = Name.WinAnsiEncoding if not len(diffs) else Dictionary(
        Type=Name.Encoding, BaseEncoding=Name.WinAnsiEncoding, Differences=diffs)

    logging.getLogger("fontTools").setLevel(logging.ERROR)
    opts = subset.Options()
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = True
    opts.legacy_cmap = True
    opts.drop_tables += ["GSUB", "GPOS", "GDEF", "kern", "DSIG"]
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=sorted(chars))
    sub.subset(tt)
    buf = io.BytesIO()
    tt.save(buf)

    family, style = _FAMILIES[std]
    ps_name = f"{_subset_tag()}+Liberation{family}-{style}"
    head, hhea, post = tt["head"], tt["hhea"], tt["post"]
    os2 = tt["OS/2"] if "OS/2" in tt else None
    scale = 1000 / upem
    flags = 32  # nonsymbolic
    if family == "Serif":
        flags |= 2
    if family == "Mono":
        flags |= 1
    if "Italic" in style:
        flags |= 64
    first, last_code = min(widths), max(widths)
    descriptor = Dictionary(
        Type=Name.FontDescriptor,
        FontName=Name("/" + ps_name),
        Flags=flags,
        FontBBox=Array([round(head.xMin * scale), round(head.yMin * scale), round(head.xMax * scale),
                        round(head.yMax * scale)]),
        ItalicAngle=float(post.italicAngle),
        Ascent=round(hhea.ascent * scale),
        Descent=round(hhea.descent * scale),
        CapHeight=round((getattr(os2, "sCapHeight", 0) or hhea.ascent) * scale),
        StemV=120 if "Bold" in style else 80,
        FontWeight=700 if "Bold" in style else 400,
        FontFile2=pdf.make_stream(buf.getvalue()),
    )
    font.Subtype = Name.TrueType
    font.BaseFont = Name("/" + ps_name)
    font.FirstChar = first
    font.LastChar = last_code
    font.Widths = Array([widths.get(c, 0) for c in range(first, last_code + 1)])
    font.Encoding = encoding
    font.FontDescriptor = pdf.make_indirect(descriptor)
    if "/ToUnicode" in font:
        pass  # keep the author's mapping
    return True


def embed_standard_fonts(pdf: pikepdf.Pdf) -> int:
    """Embed Liberation fonts for unembedded standard fonts. Returns how many."""
    if not available():
        return 0
    seen: set[tuple[int, int]] = set()
    count = 0
    used = _used_codes(pdf)

    def visit_resources(res, depth: int = 0) -> None:
        nonlocal count
        if res is None or depth > 8:
            return
        fonts = res.get("/Font")
        if fonts is not None:
            for _, font in fonts.items():
                if not isinstance(font, Dictionary) or not font.is_indirect or font.objgen in seen:
                    continue
                seen.add(font.objgen)
                try:
                    if _embed_one(pdf, font, used.get(font.objgen, set())):
                        count += 1
                except Exception:  # noqa: BLE001 - leave a font alone if anything goes wrong
                    continue
        xobjects = res.get("/XObject")
        if xobjects is not None:
            for _, x in xobjects.items():
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form and x.is_indirect:
                    if x.objgen in seen:
                        continue
                    seen.add(x.objgen)
                    visit_resources(x.get("/Resources"), depth + 1)

    for page in pdf.pages:
        visit_resources(page.obj.get("/Resources"))
    return count
