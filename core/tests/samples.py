# SPDX-License-Identifier: Apache-2.0
"""Generate small untagged PDFs that look like typical local-government files."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

LOREM = (
    "The Town Council met in regular session to review the annual budget, hear public "
    "comment on the proposed park improvements, and consider the library's request for "
    "extended weekend hours. Residents may submit written comments to the Town Clerk."
)


def _wrap(c: canvas.Canvas, text: str, x: float, y: float, width: float, font: str, size: float,
          leading: float) -> float:
    words = text.split()
    line = ""
    for word in words:
        trial = (line + " " + word).strip()
        if c.stringWidth(trial, font, size) > width and line:
            c.setFont(font, size)
            c.drawString(x, y, line)
            y -= leading
            line = word
        else:
            line = trial
    if line:
        c.setFont(font, size)
        c.drawString(x, y, line)
        y -= leading
    return y


def _chart_png() -> ImageReader:
    img = Image.new("RGB", (300, 180), "white")
    d = ImageDraw.Draw(img)
    for i, h in enumerate([60, 120, 90, 150]):
        d.rectangle([30 + i * 65, 170 - h, 75 + i * 65, 170], fill=(20, 110, 140))
    d.line([20, 170, 290, 170], fill="black", width=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return ImageReader(buf)


def council_minutes(path: str | Path, *, title_metadata: str | None = None) -> Path:
    """Three pages: heading hierarchy, list, figure, ruled table, link, footers,
    and a two-column page."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=letter)
    if title_metadata is not None:
        c.setTitle(title_metadata)
    W, H = letter

    def footer(n: int) -> None:
        c.setFont("Helvetica", 9)
        c.drawString(72, 40, "Town of Example - Council Minutes")
        c.drawRightString(W - 72, 40, f"Page {n} of 3")

    # Page 1
    c.setFont("Helvetica-Bold", 22)
    c.drawString(72, H - 90, "Regular Council Meeting Minutes")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, H - 130, "Call to Order")
    y = _wrap(c, LOREM, 72, H - 155, W - 144, "Helvetica", 11, 14)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, y - 18, "Agenda Items")
    y -= 42
    for item in ["Approval of the March minutes", "Budget amendment for road repairs",
                 "Library weekend hours pilot"]:
        c.setFont("Helvetica", 11)
        c.drawString(80, y, "•")
        c.drawString(94, y, item)
        y -= 16
    y -= 10
    c.drawImage(_chart_png(), 72, y - 180, width=300, height=180)
    y -= 200
    c.setFont("Helvetica", 11)
    c.drawString(72, y, "Full agenda packets are online at")
    link_x = 72 + c.stringWidth("Full agenda packets are online at ", "Helvetica", 11)
    c.drawString(link_x, y, "example.gov/agendas")
    c.linkURL("https://example.gov/agendas", (link_x, y - 3, link_x + 110, y + 11), relative=0)
    footer(1)
    c.showPage()

    # Page 2: ruled table
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, H - 90, "Budget Summary")
    rows = [["Department", "2025", "2026"], ["Parks", "$120,000", "$135,000"],
            ["Library", "$310,000", "$318,500"], ["Public Works", "$1,200,000", "$1,150,000"]]
    x0, top, colw, rowh = 72, H - 120, 150, 22
    for r, row in enumerate(rows):
        for k, val in enumerate(row):
            c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 11)
            c.drawString(x0 + k * colw + 6, top - (r + 1) * rowh + 7, val)
    c.setLineWidth(0.8)
    for r in range(len(rows) + 1):
        c.line(x0, top - r * rowh, x0 + 3 * colw, top - r * rowh)
    for k in range(4):
        c.line(x0 + k * colw, top, x0 + k * colw, top - len(rows) * rowh)
    y = top - len(rows) * rowh - 30
    _wrap(c, "The budget was approved unanimously after discussion of the road repair schedule.",
          72, y, W - 144, "Helvetica", 11, 14)
    footer(2)
    c.showPage()

    # Page 3: two columns
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, H - 90, "Public Comment")
    colw = (W - 144 - 24) / 2
    left = "First column. " + LOREM + " " + LOREM
    right = "Second column. " + LOREM
    _wrap(c, left, 72, H - 120, colw, "Helvetica", 11, 14)
    _wrap(c, right, 72 + colw + 24, H - 120, colw, "Helvetica", 11, 14)
    footer(3)
    c.showPage()
    c.save()
    return path


def scanned_page(path: str | Path) -> Path:
    """A page that is only a picture of text (no real text)."""
    path = Path(path)
    img = Image.new("L", (850, 1100), 255)
    d = ImageDraw.Draw(img)
    for i in range(30):
        d.rectangle([80, 100 + i * 30, 700, 112 + i * 30], fill=40)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawImage(ImageReader(buf), 0, 0, width=letter[0], height=letter[1])
    c.showPage()
    c.save()
    return path


if __name__ == "__main__":  # pragma: no cover
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    council_minutes(out / "council.pdf")
    scanned_page(out / "scanned.pdf")


def browser_style(path: str | Path) -> Path:
    """Bullets drawn as shapes (like Chrome's PDFs) and a table with no lines."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=letter)
    W, H = letter
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, H - 80, "Library Hours")
    y = H - 120
    for item in ["Monday to Friday", "Saturday mornings", "Closed on holidays"]:
        c.circle(80, y + 3.5, 2, stroke=0, fill=1)
        c.setFont("Helvetica", 11)
        c.drawString(92, y, item)
        y -= 16
    y -= 20
    rows = [["Branch", "Opens", "Closes"], ["Main", "9:00", "20:00"], ["North", "10:00", "18:00"],
            ["South", "10:00", "17:00"]]
    for r, row in enumerate(rows):
        for k, val in enumerate(row):
            c.setFont("Helvetica-Bold" if r == 0 else "Helvetica", 11)
            c.drawString(72 + k * 120, y - r * 16, val)
    c.showPage()
    c.save()
    return path
