# SPDX-License-Identifier: Apache-2.0
"""Decode text drawn with PDF fonts.

Readpath needs two things from a font: the Unicode text a string draws (so it
can tell headings from body text and show previews) and how far each glyph
advances (so it can work out where text sits on the page). This module covers
simple fonts (Type1, TrueType, Type3) and composite (Type0/CID) fonts, using
ToUnicode CMaps when present and standard encodings otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import pikepdf
from pdfminer import latin_enc
from pdfminer.encodingdb import name2unicode
from pdfminer.fontmetrics import FONT_METRICS

# code -> glyph name tables for the standard Latin encodings
_STD: dict[int, str] = {}
_MAC: dict[int, str] = {}
_WIN: dict[int, str] = {}
_PDF: dict[int, str] = {}
for _name, _std, _mac, _win, _pdf in latin_enc.ENCODING:
    if _std is not None:
        _STD[_std] = _name
    if _mac is not None:
        _MAC[_mac] = _name
    if _win is not None:
        _WIN[_win] = _name
    if _pdf is not None:
        _PDF[_pdf] = _name

# PDF 32000-1 Annex D: unused WinAnsiEncoding codes above 040 draw a bullet.
for _code in (0o177, 0o201, 0o215, 0o217, 0o220, 0o235):
    _WIN.setdefault(_code, "bullet")

_BASE_ENCODINGS = {
    "/StandardEncoding": _STD,
    "/MacRomanEncoding": _MAC,
    "/WinAnsiEncoding": _WIN,
    "/PDFDocEncoding": _PDF,
}

# Base-14 font name aliases seen in the wild
_BASE14_ALIASES = {
    "Arial": "Helvetica",
    "Arial,Bold": "Helvetica-Bold",
    "Arial,Italic": "Helvetica-Oblique",
    "Arial,BoldItalic": "Helvetica-BoldOblique",
    "TimesNewRoman": "Times-Roman",
    "TimesNewRoman,Bold": "Times-Bold",
    "TimesNewRoman,Italic": "Times-Italic",
    "TimesNewRoman,BoldItalic": "Times-BoldItalic",
    "CourierNew": "Courier",
    "CourierNew,Bold": "Courier-Bold",
}

_BOLD_RE = re.compile(r"bold|black|heavy|semibold|demibold|extrabold|ultrabold|,b\b|-bd\b", re.I)
_ITALIC_RE = re.compile(r"italic|oblique|,i\b|-it\b", re.I)


def glyph_to_unicode(name: str) -> str:
    """Map a glyph name such as 'bullet', 'uni2022' or 'f_i' to text."""
    if not name:
        return ""
    if name.startswith("/"):
        name = name[1:]
    base = name.split(".", 1)[0]
    if "_" in base and len(base) > 1:
        return "".join(glyph_to_unicode(part) for part in base.split("_"))
    try:
        return name2unicode(base)
    except Exception:  # noqa: BLE001 - unknown glyph names are common
        return ""


# ---------------------------------------------------------------------------
# CMap parsing (ToUnicode and embedded encoding CMaps)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(rb"<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/<>\[\]()]+|[^\s<>\[\]/()]+|\((?:\\.|[^\\)])*\)")


def _hex(tok: bytes) -> bytes:
    return bytes.fromhex(re.sub(rb"\s", b"", tok[1:-1]).decode("ascii") or "")


def _utf16(data: bytes) -> str:
    if len(data) % 2:
        data = b"\x00" + data
    try:
        return data.decode("utf-16-be", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


@dataclass
class CMap:
    """A parsed CMap: code space ranges, code->unicode and code->CID maps."""

    codespace: list[tuple[int, bytes, bytes]] = field(default_factory=list)  # (nbytes, lo, hi)
    to_unicode: dict[bytes, str] = field(default_factory=dict)
    to_cid: dict[bytes, int] = field(default_factory=dict)
    cid_ranges: list[tuple[bytes, bytes, int]] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes) -> "CMap":
        cmap = cls()
        tokens = _TOKEN_RE.findall(data)
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok == b"begincodespacerange":
                i += 1
                while i + 1 < n and tokens[i] != b"endcodespacerange":
                    lo, hi = _hex(tokens[i]), _hex(tokens[i + 1])
                    cmap.codespace.append((len(lo), lo, hi))
                    i += 2
            elif tok == b"beginbfchar":
                i += 1
                while i + 1 < n and tokens[i] != b"endbfchar":
                    src, dst = tokens[i], tokens[i + 1]
                    if src.startswith(b"<") and dst.startswith(b"<"):
                        cmap.to_unicode[_hex(src)] = _utf16(_hex(dst))
                    elif src.startswith(b"<") and dst.startswith(b"/"):
                        cmap.to_unicode[_hex(src)] = glyph_to_unicode(dst.decode("latin-1"))
                    i += 2
            elif tok == b"beginbfrange":
                i += 1
                while i + 2 < n and tokens[i] != b"endbfrange":
                    lo, hi = _hex(tokens[i]), _hex(tokens[i + 1])
                    i += 2
                    if tokens[i] == b"[":
                        i += 1
                        values = []
                        while i < n and tokens[i] != b"]":
                            if tokens[i].startswith(b"<"):
                                values.append(_utf16(_hex(tokens[i])))
                            i += 1
                        i += 1
                        _fill_array(cmap.to_unicode, lo, hi, values)
                    else:
                        dst = tokens[i]
                        i += 1
                        if dst.startswith(b"<"):
                            _fill_range(cmap.to_unicode, lo, hi, _hex(dst))
                continue
            elif tok == b"begincidchar":
                i += 1
                while i + 1 < n and tokens[i] != b"endcidchar":
                    try:
                        cmap.to_cid[_hex(tokens[i])] = int(tokens[i + 1])
                    except ValueError:
                        pass
                    i += 2
            elif tok == b"begincidrange":
                i += 1
                while i + 2 < n and tokens[i] != b"endcidrange":
                    try:
                        cmap.cid_ranges.append((_hex(tokens[i]), _hex(tokens[i + 1]), int(tokens[i + 2])))
                    except ValueError:
                        pass
                    i += 3
            i += 1
        return cmap

    def code_lengths(self) -> list[int]:
        return sorted({nb for nb, _, _ in self.codespace})

    def split_codes(self, data: bytes, default_len: int) -> list[bytes]:
        """Split a string into character codes using the code space ranges."""
        if not self.codespace:
            step = default_len
            return [data[i : i + step] for i in range(0, len(data) - step + 1, step)] or (
                [data] if data else []
            )
        out: list[bytes] = []
        i = 0
        while i < len(data):
            for nb, lo, hi in sorted(self.codespace, key=lambda r: r[0]):
                chunk = data[i : i + nb]
                if len(chunk) == nb and all(lo[k] <= chunk[k] <= hi[k] for k in range(nb)):
                    out.append(chunk)
                    i += nb
                    break
            else:
                nb = self.codespace[0][0]
                out.append(data[i : i + nb])
                i += nb
        return out

    def cid(self, code: bytes) -> int:
        if code in self.to_cid:
            return self.to_cid[code]
        for lo, hi, start in self.cid_ranges:
            if len(code) == len(lo) and lo <= code <= hi:
                return start + int.from_bytes(code, "big") - int.from_bytes(lo, "big")
        return int.from_bytes(code, "big")


def _fill_range(target: dict[bytes, str], lo: bytes, hi: bytes, dst: bytes) -> None:
    start, end = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
    if end - start > 65535:
        return
    base = int.from_bytes(dst, "big")
    for k, code in enumerate(range(start, end + 1)):
        value = (base + k).to_bytes(max(len(dst), 2), "big")
        target[code.to_bytes(len(lo), "big")] = _utf16(value)


def _fill_array(target: dict[bytes, str], lo: bytes, hi: bytes, values: list[str]) -> None:
    start, end = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
    for k, code in enumerate(range(start, min(end, start + len(values) - 1) + 1)):
        target[code.to_bytes(len(lo), "big")] = values[k]


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@dataclass
class Glyph:
    code: bytes
    text: str
    width: float  # advance in text space units (already divided by 1000)
    is_space: bool  # single-byte code 32, which receives word spacing


class FontInfo:
    """What Readpath needs to know about one font resource."""

    def __init__(self, font: pikepdf.Object | None):
        self.obj = font
        self.subtype = "/Type1"
        self.base_name = "Unknown"
        self.bold = False
        self.italic = False
        self.embedded = True
        self.has_tounicode = False
        self.ascent = 0.75
        self.descent = -0.25
        self.composite = False
        self._cmap: CMap | None = None
        self._encoding_cmap: CMap | None = None
        self._simple_names: dict[int, str] = {}
        self._widths: dict[int, float] = {}
        self._default_width = 0.5
        self._cid_widths: dict[int, float] = {}
        self._cid_default = 1.0
        self._cid_unicode = None
        self._std_metrics: dict[str, float] | None = None
        self._font_matrix_scale = 0.001
        self._code_len = 1
        if font is None:
            return
        try:
            self._load(font)
        except Exception:  # noqa: BLE001 - damaged fonts must not stop analysis
            pass

    # -- loading -----------------------------------------------------------
    def _load(self, font: pikepdf.Object) -> None:
        self.subtype = str(font.get("/Subtype", "/Type1"))
        self.base_name = str(font.get("/BaseFont", "/Unknown"))[1:] or "Unknown"
        clean = self.base_name.split("+", 1)[-1]
        self.bold = bool(_BOLD_RE.search(clean))
        self.italic = bool(_ITALIC_RE.search(clean))

        descriptor = font.get("/FontDescriptor")
        if self.subtype == "/Type0":
            self.composite = True
            descendants = font.get("/DescendantFonts")
            desc_font = descendants[0] if descendants is not None and len(descendants) else None
            if desc_font is not None:
                descriptor = desc_font.get("/FontDescriptor", descriptor)
                self._load_cid_widths(desc_font)
                info = desc_font.get("/CIDSystemInfo")
                if info is not None:
                    ordering = str(info.get("/Ordering", ""))
                    registry = str(info.get("/Registry", ""))
                    if ordering and ordering != "Identity" and registry == "Adobe":
                        try:
                            from pdfminer.cmapdb import CMapDB

                            self._cid_unicode = CMapDB.get_unicode_map(f"Adobe-{ordering}", False)
                        except Exception:  # noqa: BLE001
                            self._cid_unicode = None
            encoding = font.get("/Encoding")
            self._code_len = 2
            if isinstance(encoding, pikepdf.Stream):
                try:
                    self._encoding_cmap = CMap.parse(encoding.read_bytes())
                except Exception:  # noqa: BLE001
                    self._encoding_cmap = None
        else:
            self._load_simple(font, descriptor)

        if descriptor is not None:
            flags = int(descriptor.get("/Flags", 0) or 0)
            if flags & (1 << 18):  # ForceBold
                self.bold = True
            if flags & (1 << 6):  # Italic
                self.italic = True
            weight = descriptor.get("/FontWeight")
            if weight is not None and float(weight) >= 600:
                self.bold = True
            self.embedded = any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            asc = descriptor.get("/Ascent")
            desc = descriptor.get("/Descent")
            if asc is not None and float(asc) > 0:
                self.ascent = min(float(asc) / 1000.0, 1.2)
            if desc is not None and float(desc) < 0:
                self.descent = max(float(desc) / 1000.0, -0.6)
        elif self.subtype != "/Type3":
            self.embedded = False

        tounicode = font.get("/ToUnicode")
        if isinstance(tounicode, pikepdf.Stream):
            try:
                self._cmap = CMap.parse(tounicode.read_bytes())
                self.has_tounicode = bool(self._cmap.to_unicode)
            except Exception:  # noqa: BLE001
                self._cmap = None

    def _load_simple(self, font: pikepdf.Object, descriptor) -> None:
        std_name = _BASE14_ALIASES.get(self.base_name, self.base_name.split("+", 1)[-1])
        if std_name in FONT_METRICS:
            desc, widths = FONT_METRICS[std_name]
            self._std_metrics = {k: float(v) / 1000.0 for k, v in widths.items()}
            self.ascent = float(desc.get("Ascent", 750)) / 1000.0
            self.descent = float(desc.get("Descent", -250)) / 1000.0
            if "Bold" in std_name:
                self.bold = True

        # Encoding: base table plus Differences
        names: dict[int, str] = {}
        encoding = font.get("/Encoding")
        base_table = _STD
        if self.subtype == "/TrueType":
            base_table = _WIN
        if std_name in ("Symbol", "ZapfDingbats"):
            base_table = {}
        if isinstance(encoding, pikepdf.Name):
            base_table = _BASE_ENCODINGS.get(str(encoding), base_table)
        elif isinstance(encoding, pikepdf.Dictionary):
            base = encoding.get("/BaseEncoding")
            if base is not None:
                base_table = _BASE_ENCODINGS.get(str(base), base_table)
        names.update(base_table)
        if isinstance(encoding, pikepdf.Dictionary):
            diffs = encoding.get("/Differences")
            if diffs is not None:
                code = 0
                for item in diffs:
                    if isinstance(item, pikepdf.Name):
                        names[code] = str(item)[1:]
                        code += 1
                    else:
                        try:
                            code = int(item)
                        except (TypeError, ValueError):
                            pass
        self._simple_names = names

        first = int(font.get("/FirstChar", 0) or 0)
        widths = font.get("/Widths")
        if widths is not None:
            for k, w in enumerate(widths):
                try:
                    self._widths[first + k] = float(w)
                except (TypeError, ValueError):
                    continue
        if descriptor is not None and descriptor.get("/MissingWidth") is not None:
            self._default_width = float(descriptor.get("/MissingWidth")) / 1000.0
        if self.subtype == "/Type3":
            matrix = font.get("/FontMatrix")
            if matrix is not None:
                try:
                    self._font_matrix_scale = abs(float(matrix[0])) or 0.001
                except (TypeError, ValueError, IndexError):
                    pass
            self.embedded = True

    def _load_cid_widths(self, desc_font: pikepdf.Object) -> None:
        dw = desc_font.get("/DW")
        if dw is not None:
            self._cid_default = float(dw) / 1000.0
        w = desc_font.get("/W")
        if w is None:
            return
        items = list(w)
        i = 0
        while i < len(items):
            try:
                start = int(items[i])
            except (TypeError, ValueError):
                break
            nxt = items[i + 1] if i + 1 < len(items) else None
            if isinstance(nxt, pikepdf.Array):
                for k, val in enumerate(nxt):
                    self._cid_widths[start + k] = float(val) / 1000.0
                i += 2
            elif i + 2 < len(items):
                end, val = int(items[i + 1]), float(items[i + 2]) / 1000.0
                if end - start <= 70000:
                    for cid in range(start, end + 1):
                        self._cid_widths[cid] = val
                i += 3
            else:
                break

    # -- decoding ----------------------------------------------------------
    def decode(self, data: bytes) -> list[Glyph]:
        """Turn a string operand into glyphs with Unicode text and advances."""
        glyphs: list[Glyph] = []
        if self.composite:
            splitter = self._encoding_cmap or self._cmap or CMap()
            codes = splitter.split_codes(data, self._code_len)
            for code in codes:
                cid = self._encoding_cmap.cid(code) if self._encoding_cmap else int.from_bytes(code, "big")
                text = ""
                if self._cmap is not None:
                    text = self._cmap.to_unicode.get(code, "")
                if not text and self._cid_unicode is not None:
                    try:
                        text = self._cid_unicode.get_unichr(cid)
                    except Exception:  # noqa: BLE001
                        text = ""
                width = self._cid_widths.get(cid, self._cid_default)
                glyphs.append(Glyph(code, text, width, len(code) == 1 and code == b" "))
            return glyphs

        for byte in data:
            code = bytes([byte])
            text = ""
            if self._cmap is not None:
                text = self._cmap.to_unicode.get(code, "")
            name = self._simple_names.get(byte)
            if not text and name:
                text = glyph_to_unicode(name)
            if not text and not self._simple_names and 32 <= byte < 127:
                text = chr(byte)
            if byte in self._widths:
                width = self._widths[byte] * (self._font_matrix_scale if self.subtype == "/Type3" else 0.001)
            elif self._std_metrics is not None and text and text[0] in self._std_metrics:
                width = self._std_metrics[text[0]]
            else:
                width = self._default_width
            glyphs.append(Glyph(code, text, width, byte == 32))
        return glyphs

    @property
    def unicode_ok(self) -> bool:
        """True when text can be mapped to Unicode reliably (PDF/UA 7.21.7)."""
        if self.has_tounicode:
            return True
        if self.composite:
            return self._cid_unicode is not None
        if self.subtype == "/Type3":
            return bool(self._simple_names)
        return bool(self._simple_names) or self.base_name in FONT_METRICS


@lru_cache(maxsize=1)
def _empty_font() -> FontInfo:
    return FontInfo(None)


class FontCache:
    """Caches FontInfo by indirect object id so each font is parsed once."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int], FontInfo] = {}

    def get(self, font: pikepdf.Object | None) -> FontInfo:
        if font is None:
            return _empty_font()
        if not font.is_indirect:
            return FontInfo(font)
        key = font.objgen
        info = self._cache.get(key)
        if info is None:
            info = FontInfo(font)
            self._cache[key] = info
        return info
