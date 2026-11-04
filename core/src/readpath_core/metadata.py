# SPDX-License-Identifier: Apache-2.0
"""Document-level metadata: title, language, viewer preferences, PDF/UA mark,
and the small markers Readpath leaves to remember what a person reviewed."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pikepdf
from pikepdf import Dictionary

# Info-dictionary keys Readpath uses to remember its own work. Custom Info keys
# are allowed by PDF 1.x and do not affect PDF/A or PDF/UA conformance.
AUTOTAGGED_KEY = "/ReadpathAutoTagged"
REVIEWED_KEY = "/ReadpathReviewed"
TITLE_AUTO_KEY = "/ReadpathTitleGuessed"
REVIEW_STEPS = ("basics", "reading-order", "images", "tables")


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
        if value is None:
            return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def get_title(pdf: pikepdf.Pdf) -> str | None:
    try:
        meta = pdf.open_metadata()
        title = _clean(meta.get("dc:title"))
        if title:
            return title
    except Exception:  # noqa: BLE001 - malformed XMP is common
        pass
    try:
        return _clean(pdf.docinfo.get("/Title"))
    except Exception:  # noqa: BLE001
        return None


def xmp_title(pdf: pikepdf.Pdf) -> str | None:
    try:
        return _clean(pdf.open_metadata().get("dc:title"))
    except Exception:  # noqa: BLE001
        return None


def set_title(pdf: pikepdf.Pdf, title: str, *, guessed: bool = False) -> None:
    title = re.sub(r"\s+", " ", title).strip()[:300]
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = title
    except Exception:  # noqa: BLE001
        pass
    pdf.docinfo["/Title"] = pikepdf.String(title)
    if guessed:
        pdf.docinfo[TITLE_AUTO_KEY] = pikepdf.String("true")
    elif TITLE_AUTO_KEY in pdf.docinfo:
        del pdf.docinfo[TITLE_AUTO_KEY]


def title_guessed(pdf: pikepdf.Pdf) -> bool:
    try:
        return TITLE_AUTO_KEY in pdf.docinfo
    except Exception:  # noqa: BLE001
        return False


def get_lang(pdf: pikepdf.Pdf) -> str | None:
    v = pdf.Root.get("/Lang")
    return _clean(v) if v is not None else None


def set_lang(pdf: pikepdf.Pdf, lang: str) -> None:
    pdf.Root.Lang = pikepdf.String(lang.strip())
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:language"] = [lang.strip()]
    except Exception:  # noqa: BLE001
        pass


def display_title(pdf: pikepdf.Pdf) -> bool:
    vp = pdf.Root.get("/ViewerPreferences")
    return bool(vp is not None and vp.get("/DisplayDocTitle", False))


def set_display_title(pdf: pikepdf.Pdf) -> None:
    vp = pdf.Root.get("/ViewerPreferences")
    if vp is None:
        pdf.Root.ViewerPreferences = Dictionary(DisplayDocTitle=True)
    else:
        vp.DisplayDocTitle = True


def pdfua_marked(pdf: pikepdf.Pdf) -> bool:
    try:
        return str(pdf.open_metadata().get("pdfuaid:part", "")).strip() in ("1", "2")
    except Exception:  # noqa: BLE001
        return False


def set_pdfua(pdf: pikepdf.Pdf) -> None:
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["pdfuaid:part"] = "1"
        if not _clean(meta.get("dc:title")):
            t = _clean(pdf.docinfo.get("/Title"))
            if t:
                meta["dc:title"] = t


def mark_autotagged(pdf: pikepdf.Pdf) -> None:
    pdf.docinfo[AUTOTAGGED_KEY] = pikepdf.String(_dt.date.today().isoformat())
    if REVIEWED_KEY in pdf.docinfo:
        del pdf.docinfo[REVIEWED_KEY]


def autotagged(pdf: pikepdf.Pdf) -> bool:
    try:
        return AUTOTAGGED_KEY in pdf.docinfo
    except Exception:  # noqa: BLE001
        return False


def reviewed_steps(pdf: pikepdf.Pdf) -> set[str]:
    try:
        raw = str(pdf.docinfo.get(REVIEWED_KEY, ""))
    except Exception:  # noqa: BLE001
        return set()
    return {s for s in raw.split() if s in REVIEW_STEPS}


def mark_reviewed(pdf: pikepdf.Pdf, step: str, done: bool = True) -> None:
    steps = reviewed_steps(pdf)
    if done:
        steps.add(step)
    else:
        steps.discard(step)
    if steps:
        pdf.docinfo[REVIEWED_KEY] = pikepdf.String(" ".join(s for s in REVIEW_STEPS if s in steps))
    elif REVIEWED_KEY in pdf.docinfo:
        del pdf.docinfo[REVIEWED_KEY]


_JUNK_TITLE = re.compile(
    r"^(untitled|microsoft (word|excel|powerpoint)|document\d*|doc\d*|title|new document|"
    r"slide \d+|presentation\d*|book\d*|sheet\d*|[\w\-]+\.(docx?|xlsx?|pptx?|pdf|indd|rtf))$",
    re.I,
)


def is_meaningful_title(title: str | None) -> bool:
    if not title:
        return False
    t = title.strip()
    if len(t) < 3 or _JUNK_TITLE.match(t):
        return False
    if re.fullmatch(r"[\W\d_]+", t):
        return False
    return True


def title_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = Path(filename).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if len(stem) < 3 or re.fullmatch(r"[\W\d_ ]+", stem):
        return None
    return stem[:1].upper() + stem[1:]
