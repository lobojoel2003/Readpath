# SPDX-License-Identifier: Apache-2.0
"""Render pages and regions to PNG with PDFium (via pypdfium2)."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pypdfium2 as pdfium

# PDFium is not thread-safe; serialize all calls into it.
_LOCK = threading.Lock()


def page_png(path: str | Path, page_index: int, width: int = 900) -> bytes:
    width = max(100, min(int(width), 2400))
    with _LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            if not 0 <= page_index < len(doc):
                raise IndexError("page out of range")
            page = doc[page_index]
            w, _ = page.get_size()
            scale = width / max(w, 1.0)
            image = page.render(scale=scale, may_draw_forms=True).to_pil()
            page.close()
        finally:
            doc.close()
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def region_png(path: str | Path, page_index: int, bbox: list[float] | tuple[float, ...] | None,
               width: int = 600, pad: float = 0.01) -> bytes:
    """Crop of a page; `bbox` is [left, top, right, bottom] as page fractions."""
    with _LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[page_index]
            w, h = page.get_size()
            if bbox:
                frac_w = max(0.02, (bbox[2] - bbox[0]) + 2 * pad)
            else:
                frac_w = 1.0
            scale = min(4.0, max(0.5, width / max(w * frac_w, 1.0)))
            image = page.render(scale=scale, may_draw_forms=True).to_pil()
            page.close()
        finally:
            doc.close()
    if bbox:
        iw, ih = image.size
        left = max(0, int((bbox[0] - pad) * iw))
        top = max(0, int((bbox[1] - pad) * ih))
        right = min(iw, int((bbox[2] + pad) * iw) + 1)
        bottom = min(ih, int((bbox[3] + pad) * ih) + 1)
        if right - left > 2 and bottom - top > 2:
            image = image.crop((left, top, right, bottom))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()
