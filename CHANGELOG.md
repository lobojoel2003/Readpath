# Changelog

All notable changes to Readpath are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each release covers the app
(`readpath-server`, web app, Docker image) and the engine (`readpath-core`),
which share one version number.

## [Unreleased]

## [0.1.0] - 2026-11-04

First release: crawl a website, list every PDF, score each one, fix what is
safe automatically, and walk a person through the rest.

### Added

- **Website crawler** that finds every linked document, including CMS links
  with no `.pdf` in the address (CivicPlus `DocumentCenter`, `AgendaCenter`).
  Honors robots.txt, reads sitemaps, stays on your site, and lists Word, Excel
  and PowerPoint files for the inventory.
- **20 accessibility checks** mapped to WCAG 2.1 and PDF/UA-1 (ISO 14289-1),
  written in plain language, with a 0–100 score and a grade: *Ready*,
  *Can fix automatically* or *Needs your help*.
- **Automatic tagging** of untagged PDFs: headings with levels, paragraphs,
  lists (text and vector bullets), ruled and unruled tables with header cells,
  images and vector charts, links, form fields, and page headers, footers and
  page numbers marked as decoration. Reading order comes from the layout,
  including multi-column pages. Pages look the same afterwards.
- **Other safe fixes**: document language (detected from the text), title
  (from the first heading), "show title" setting, link descriptions, tab
  order, bookmarks for long documents, removal of security settings that block
  screen readers, exact-width font substitutes for unembedded Helvetica, Times
  and Courier, and OCR for scanned pages (with OCRmyPDF).
- **Guided review** for what software can't decide: title and language,
  reading order on the page image (reorder, set heading levels, remove
  headers and footers), image descriptions or "decorative", and table headers.
  Optional draft descriptions from a local vision model (Ollama or any
  OpenAI-compatible server).
- **PDF/UA identifier** added when a reviewed file passes every automatic check.
- **veraPDF integration** for a second opinion from the reference PDF/UA validator.
- **Accessible web app** (WCAG 2.1 AA, keyboard-only friendly, light and dark themes).
- **HTTP API** ([docs/api.md](docs/api.md)), CSV export of scan results, and an
  optional shared password.
- **`readpath` command line** (`check`, `fix`) and Python library in `readpath-core`.
- **Docker image** with veraPDF, OCRmyPDF and the Liberation fonts;
  `docker compose up -d` to self-host, plus an optional Ollama add-on.
- **Licensing**: the app is AGPL-3.0-or-later; the `readpath-core` engine is Apache-2.0.

[Unreleased]: https://github.com/lobojoel2003/Readpath/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lobojoel2003/Readpath/releases/tag/v0.1.0
