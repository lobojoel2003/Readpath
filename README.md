# Readpath

**Find every PDF on your website, see which ones screen-reader users can't read, fix the safe problems automatically, and get walked through the rest.**

Readpath is a free, open-source, self-hosted app for small governments, school
districts, libraries and nonprofits that need their documents to meet
**WCAG 2.1 AA** under the DOJ's ADA Title II rule, and have no budget for
remediation vendors.

| Who | Deadline to meet WCAG 2.1 AA |
|---|---|
| State and local governments serving 50,000+ people | **April 26, 2027** |
| Smaller governments and special districts | **April 26, 2028** |

## What it does

1. **Crawl** your website (or upload PDFs) and list every document it links to,
   including CMS links with no `.pdf` in the address (CivicPlus `DocumentCenter`,
   `AgendaCenter` and similar). Word, Excel and PowerPoint files are listed too.
2. **Score** each PDF from 0 to 100 against 20 checks mapped to WCAG 2.1 and
   PDF/UA-1 (ISO 14289-1), in plain language: *"This file has no tags, so screen
   readers read it as one long, unstructured stream."*
3. **Fix automatically** what is safe to fix. The page looks exactly the same
   afterwards (Readpath's tests render every page before and after and compare
   the pixels):
   - **adds tags to untagged PDFs**: headings (with levels), paragraphs,
     bulleted and numbered lists, tables (ruled and unruled) with header cells,
     images, links, and page headers, footers and page numbers marked as decoration.
     Reading order is worked out from the layout, including multi-column pages
   - sets the document language (detected from the text) and the title
     (from the first heading), and shows the title instead of the file name
   - describes and tags links, tags form fields and comments, fixes the tab order
   - adds bookmarks to long documents, using the headings
   - removes security settings that lock screen readers out
   - runs text recognition (OCR) on scanned pages, when OCRmyPDF is installed (it is in the Docker image)
4. **Guided review** for what software can't decide alone, with the page image
   beside each step:
   - **Title and language**
   - **Reading order**: numbered boxes on the page. Move blocks up and down,
     mark headings, or take headers and footers out of the reading order
   - **Images**: write a description or mark the image decorative. An optional
     local vision model can draft descriptions; a person always confirms them
   - **Tables**: tick "first row has headings" and "first column has headings"
5. **Download** the accessible PDF (the original is always kept), export a CSV
   report for your records, and optionally double-check with
   [veraPDF](https://verapdf.org), the reference PDF/UA validator.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/).

```bash
git clone https://github.com/lobojoel2003/readpath.git
```

```bash
cd readpath && docker compose up -d
```

Then open **http://localhost:8000**. Your scans are kept in the `readpath-data` volume.

To have a local vision model draft image descriptions (nothing leaves your server):

```bash
docker compose -f docker-compose.yml -f docker-compose.vision.yml up -d
```

```bash
docker compose exec ollama ollama pull qwen2.5vl:3b
```

If you put Readpath on a public server, set `READPATH_PASSWORD` in
`docker-compose.yml` (everyone shares one password; use any user name).

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `READPATH_PASSWORD` | none | Require a shared password (HTTP Basic auth) |
| `READPATH_DEFAULT_LANG` | `en-US` | Language used when it can't be detected |
| `READPATH_WORKERS` | `2` | Documents checked or fixed at the same time |
| `READPATH_VERAPDF_ON_SCAN` | `false` | Run veraPDF on every crawled PDF (slower). It always runs on "Run again" and "Finish review" |
| `READPATH_VISION_URL` / `_MODEL` / `_API` | none / `qwen2.5vl:3b` / `ollama` | Vision model for draft alt text. `_API=openai` works with llama.cpp, LM Studio or vLLM |
| `READPATH_MAX_UPLOAD_MB` | `100` | Largest upload |
| `READPATH_ALLOW_PRIVATE_HOSTS` | `false` | Allow scanning sites on private networks (intranets). Off by default so a public server can't be used to probe internal addresses |
| `READPATH_DATA` | `/data` | Where the database and files are stored |

## Use the engine without the app

The PDF engine is a separate Python library, **readpath-core** (Apache-2.0),
so agencies and vendors can build it into their own systems.

```bash
pip install ./core
```

```bash
readpath check minutes.pdf
```

```bash
readpath fix minutes.pdf -o minutes-accessible.pdf
```

```python
from readpath_core import check_file, autofix_file

report = check_file("minutes.pdf")
print(report.score, report.grade)
autofix_file("minutes.pdf", "minutes-accessible.pdf")
```

The guided-review operations (reorder blocks, set alt text, table headers…)
are in `readpath_core.review.Review`. See [core/README.md](core/README.md).

## What "ready" means, and what Readpath can't do

Readpath grades each file:

- **Ready**: passes every automatic check (minor PDF/UA technicalities aside).
- **Can fix automatically**: click *Fix automatically*.
- **Needs your help**: open the guided review, or fix the source file.

No software can certify WCAG conformance on its own. Some things always need
a person: whether descriptions are accurate, whether link text makes sense,
color contrast, and whether the reading order matches the meaning. The review's
last step lists these.

Current limitations:

- **Fonts that aren't embedded** can't be fixed without the source file
  (re-export with "embed fonts" on). Readpath flags them as a minor PDF/UA issue.
- **Charts drawn as vector graphics** (not pictures) are marked as decoration.
  Describe them in the surrounding text, or re-export them as images.
- **Complex layouts** (forms, nested tables, magazine spreads) may need more
  reading-order fixes in the review.
- **Form fields without labels** are flagged but must be labelled in an editor such as Acrobat.
- **Office files** (Word, Excel, PowerPoint) are listed but not checked yet.
- Files that are already tagged keep their tags; Readpath only re-tags them
  when most of the content is untagged.

## How it works

```
web/       React + TypeScript app (AGPL-3.0)
server/    FastAPI API, SQLite, crawler, background jobs (AGPL-3.0)
core/      readpath-core: the PDF engine (Apache-2.0)
  content.py   walks content streams: decodes fonts, finds every text run, image and path
  layout.py    lines → paragraphs, headings, lists, tables, figures, headers/footers, reading order
  tagger.py    writes the structure tree and wraps content in marked content (BDC/EMC)
  checks.py    20 checks, score and grade
  fixer.py     safe automatic fixes
  review.py    guided-review edits on any tagged PDF
docs/api.md  the HTTP API
```

The tagger rewrites each page's content stream, wrapping every piece of
content either in tagged marked content (`/P <</MCID 3>> BDC … EMC`) or in
`/Artifact`. It always wraps at the deepest safe level, so tags nest correctly
with text objects, graphics state and existing marked content. Files tagged by
Readpath pass veraPDF's PDF/UA-1 machine checks, except for issues that need a
person (image descriptions) or the source file (embedded fonts).

veraPDF runs as a separate program; the Docker image fetches it from Maven
Central. OCRmyPDF and Tesseract are installed from Debian packages.

## Development

```bash
pip install -e ./core -e ./server pytest reportlab
```

```bash
cd core && pytest
```

```bash
cd server && pytest
```

```bash
cd web && npm install && npm run dev
```

Run the API with `readpath-server` (port 8000; the web dev server proxies `/api` to it).

## Licensing

- **The app** (`web/`, `server/`, Docker files): **GNU AGPL-3.0** ([LICENSE](LICENSE)).
  Anyone who runs a modified copy as a hosted service must share their changes,
  so paid remediation vendors can't resell a closed hosted copy.
- **The engine** (`core/`): **Apache-2.0** ([core/LICENSE](core/LICENSE)), so
  agencies and vendors can embed it, the same license as
  [pdf-a11y](https://pypi.org/project/pdf-a11y/).

## Related work

- [pdf-a11y](https://pypi.org/project/pdf-a11y/) (Apache-2.0): a command-line
  remediation pipeline using Python, Java and veraPDF. It quotes manual
  remediation at €50–150 per page.
- [veraPDF](https://verapdf.org): the open-source PDF/UA and PDF/A validator.
- The leading commercial remediation tools (Acrobat Pro, CommonLook, Equidox,
  PREP and others) are not open source ([Continual Engine's 2026 roundup](https://www.continualengine.com/blog/best-pdf-remediation-tools/)).
- Deadlines: [Siteimprove on ADA Title II document accessibility](https://www.siteimprove.com/blog/ada-title-ii-document-accessibility/).
