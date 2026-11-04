# readpath-core

The PDF engine behind [Readpath](https://github.com/lobojoel2003/readpath):
check, score and fix PDF accessibility (WCAG 2.1 AA / PDF/UA-1). Licensed
**Apache-2.0** so you can embed it in your own systems.

Pure Python on top of pikepdf, pdfminer.six, pdfplumber and pypdfium2. No Java needed;
veraPDF and OCRmyPDF are used when present.

## Command line

```bash
readpath check report.pdf            # human-readable report; exit code 1 if not ready
readpath check --json --verapdf *.pdf
readpath fix report.pdf -o report-accessible.pdf
```

## Python

```python
from readpath_core import check_file, autofix_file

report = check_file("report.pdf")
for check in report.checks:
    if check.status in ("fail", "warning"):
        print(check.title, "-", check.message, check.wcag)

result = autofix_file("report.pdf", "report-accessible.pdf")
print(result.applied)
```

Guided-review edits work on any tagged PDF:

```python
import pikepdf
from readpath_core.review import Review

with pikepdf.open("report-accessible.pdf", allow_overwriting_input=True) as pdf:
    review = Review(pdf)
    for block in review.page_blocks(0):          # reading order of page 1
        print(block["id"], block["type"], block["text"][:60])
    fig = review.figures()[0]
    review.set_figure(fig["id"], alt="Map of the three new bus routes")
    table = review.tables()[0]
    review.set_table_headers(table["id"], header_row=True, header_column=False)
    review.set_metadata("Transit Plan 2026", "en-US")
    pdf.save("report-accessible.pdf")
```

Element ids (`e12`) are positions in the structure tree; re-read the lists after
each edit.

## Modules

| Module | Purpose |
|---|---|
| `content` | Walks content streams; decodes text (ToUnicode, encodings, CID fonts); bounding boxes for every text run, image and path; existing marked content |
| `layout` | Lines, paragraphs, headings and levels, lists (text and vector bullets), ruled and unruled tables, figures, repeated headers/footers, reading order (content order or XY-cut) |
| `tagger` | Writes the structure tree, ParentTree and annotation links; rewrites content streams with marked content |
| `checks` | The checks, score and grade |
| `fixer` | Safe automatic fixes, OCR, bookmarks, PDF/UA identifier |
| `review` | Reading order, block types, alt text, decorative images, table headers, metadata |
| `render` | Page and region PNGs (PDFium) |
| `verapdf`, `ocr`, `vision` | Optional external tools: veraPDF, OCRmyPDF, a local vision model for draft alt text |
