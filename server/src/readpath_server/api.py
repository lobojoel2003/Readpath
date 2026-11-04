# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP API (see docs/api.md) and static hosting for the web app."""

from __future__ import annotations

import base64
import csv
import io
import logging
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from readpath_core import __version__ as core_version
from readpath_core import ocr as core_ocr
from readpath_core import render, verapdf, vision
from readpath_core.review import ReviewError

from . import __version__
from .config import Settings
from .db import now
from .engine import TERMINAL_SCAN, Conflict, Engine, NotFound, UnsafeURL, check_public_url

log = logging.getLogger("readpath")

STEP_IDS = ("basics", "reading-order", "images", "tables")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2000)
    max_pages: int | None = Field(default=None, ge=1)
    max_depth: int | None = Field(default=None, ge=0, le=20)
    include_subdomains: bool = False


class Revisioned(BaseModel):
    revision: int


class MetadataBody(Revisioned):
    title: str | None = None
    lang: str | None = None


class OrderBody(Revisioned):
    block_ids: list[str]


class BlockBody(Revisioned):
    type: str


class FigureBody(Revisioned):
    alt: str | None = None
    decorative: bool = False


class TableBody(Revisioned):
    header_row: bool
    header_column: bool


# ---------------------------------------------------------------------------
# Shaping responses
# ---------------------------------------------------------------------------


def doc_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "scan_id": doc["scan_id"],
        "filename": doc["filename"],
        "kind": doc["kind"],
        "url": doc["url"],
        "found_on": doc["found_on"],
        "status": doc["status"],
        "error": doc["error"],
        "size_bytes": doc["size_bytes"],
        "page_count": doc["page_count"],
        "title": doc["title"],
        "score": doc["score"],
        "original_score": doc["original_score"],
        "grade": doc["grade"] if doc["status"] in ("checked", "fixing") else None,
        "counts": doc["counts"] or {"failed": 0, "auto_fixable": 0, "needs_help": 0, "passed": 0},
        "fixed": bool(doc["fixed"]),
        "revision": doc["revision"],
        "updated_at": doc["updated_at"],
    }


def report_of(doc: dict[str, Any]) -> dict[str, Any]:
    report = doc.get("report") or {}
    return {
        "document": doc_summary(doc),
        "checks": report.get("checks", []),
        "verapdf": doc.get("verapdf"),
        "fixes_applied": doc.get("fixes") or [],
        "checked_at": doc.get("checked_at"),
    }


def scan_out(engine: Engine, scan: dict[str, Any]) -> dict[str, Any]:
    s = engine.db.scan_summary(scan["id"])
    return {
        "id": scan["id"],
        "kind": scan["kind"],
        "name": scan["name"],
        "start_url": scan["start_url"],
        "status": scan["status"],
        "message": scan["message"],
        "pages_crawled": scan["pages_crawled"],
        "max_pages": scan["max_pages"],
        "created_at": scan["created_at"],
        "finished_at": scan["finished_at"],
        "summary": {k: s[k] for k in ("documents", "pdfs", "checked", "average_score", "ready", "auto_fixable",
                                      "needs_help", "errors", "other_documents")},
    }


def _normalize_start(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise HTTPException(422, "Enter a website address like example.gov.")
    return url


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = Engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.start()
        yield
        await engine.stop()

    app = FastAPI(title="Readpath", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.engine = engine

    # Optional shared password (HTTP Basic, any user name)
    if settings.password:
        expected = settings.password

        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/api/health":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    _, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                    ok = secrets.compare_digest(pw, expected)
                except Exception:  # noqa: BLE001
                    ok = False
            if not ok:
                return Response("Sign in to use Readpath.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Readpath"'})
            return await call_next(request)

    @app.exception_handler(NotFound)
    async def _nf(_: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ReviewError)
    async def _review(_: Request, exc: ReviewError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # -- health ------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": f"{__version__} (core {core_version})",
                "verapdf": verapdf.available(), "vision": vision.available(), "ocr": core_ocr.ocr_available()}

    # -- scans -------------------------------------------------------------
    @app.get("/api/scans")
    def list_scans():
        return [scan_out(engine, s) for s in engine.db.list_scans()]

    @app.post("/api/scans")
    async def create_scan(body: ScanRequest):
        url = _normalize_start(body.url)
        try:
            check_public_url(url, settings.allow_private_hosts)
        except UnsafeURL as exc:
            raise HTTPException(422, str(exc)) from exc
        max_pages = min(body.max_pages or 200, settings.max_pages_limit)
        host = urlsplit(url).hostname or url
        scan = engine.db.create_scan(kind="crawl", name=re.sub(r"^www\.", "", host), start_url=url,
                                     status="queued", message="Waiting to start…", max_pages=max_pages,
                                     max_depth=body.max_depth if body.max_depth is not None else 5,
                                     include_subdomains=int(body.include_subdomains))
        engine.spawn(engine.run_crawl(scan["id"]))
        return scan_out(engine, scan)

    @app.post("/api/uploads")
    async def upload(files: list[UploadFile] = File(...), name: str | None = Form(default=None)):
        if not files:
            raise HTTPException(422, "Choose at least one PDF to upload.")
        limit = settings.max_upload_mb * 1024 * 1024
        staged: list[tuple[str, Path, int]] = []
        tmpdir = Path(tempfile.mkdtemp(dir=settings.data_dir))
        try:
            for i, f in enumerate(files):
                filename = Path(f.filename or f"document-{i + 1}.pdf").name[:150] or f"document-{i + 1}.pdf"
                dest = tmpdir / f"{i}.pdf"
                size = 0
                head = b""
                with dest.open("wb") as out:
                    while chunk := await f.read(1024 * 1024):
                        if not head:
                            head = chunk[:1024]
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, f"“{filename}” is larger than {settings.max_upload_mb} MB.")
                        out.write(chunk)
                if b"%PDF-" not in head:
                    raise HTTPException(422, f"“{filename}” is not a PDF.")
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"
                staged.append((filename, dest, size))
            label = (name or "").strip() or ("Uploaded files" if len(staged) > 1 else staged[0][0])
            scan = engine.db.create_scan(kind="upload", name=label[:200], status="checking",
                                         message="Checking…")
            ids = [engine.create_upload_doc(scan["id"], fn, path, size)["id"] for fn, path, size in staged]
        finally:
            for p in tmpdir.glob("*"):
                p.unlink(missing_ok=True)
            tmpdir.rmdir()
        engine.spawn(engine.run_uploads(scan["id"], ids))
        return scan_out(engine, engine.get_scan(scan["id"]))

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: str):
        return scan_out(engine, engine.get_scan(scan_id))

    @app.get("/api/scans/{scan_id}/documents")
    def scan_documents(scan_id: str):
        engine.get_scan(scan_id)
        return [doc_summary(d) for d in engine.db.list_documents(scan_id)]

    @app.post("/api/scans/{scan_id}/cancel")
    def cancel_scan(scan_id: str):
        scan = engine.get_scan(scan_id)
        if scan["status"] not in TERMINAL_SCAN:
            engine.db.update_scan(scan_id, cancel_requested=1, status="cancelled",
                                  message="Scan cancelled.", finished_at=now())
        return scan_out(engine, engine.get_scan(scan_id))

    @app.post("/api/scans/{scan_id}/autofix")
    async def autofix_scan(scan_id: str):
        scan = engine.get_scan(scan_id)
        if scan["status"] not in TERMINAL_SCAN:
            raise HTTPException(409, "Wait for the scan to finish first.")
        engine.spawn(engine.autofix_scan(scan_id))
        engine.db.update_scan(scan_id, status="fixing", message="Fixing documents automatically…")
        return scan_out(engine, engine.get_scan(scan_id))

    @app.delete("/api/scans/{scan_id}", status_code=204)
    def delete_scan(scan_id: str):
        engine.delete_scan(scan_id)
        return Response(status_code=204)

    @app.get("/api/scans/{scan_id}/report.csv")
    def scan_csv(scan_id: str):
        scan = engine.get_scan(scan_id)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["File", "Type", "Status", "Score", "Original score", "Result", "Pages", "Title",
                    "Problems", "Document URL", "Found on page", "Error"])
        grades = {"ready": "Ready", "auto_fixable": "Can fix automatically", "needs_help": "Needs your help"}
        for d in engine.db.list_documents(scan_id):
            checks = (d.get("report") or {}).get("checks", [])
            problems = "; ".join(c["title"] for c in checks if c["status"] in ("fail", "warning"))
            w.writerow([d["filename"], d["kind"], d["status"], d["score"], d["original_score"],
                        grades.get(d["grade"] or "", ""), d["page_count"], d["title"], problems, d["url"],
                        d["found_on"], d["error"]])
        safe = re.sub(r"[^A-Za-z0-9.-]+", "-", scan["name"]).strip("-") or "scan"
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="readpath-{safe}.csv"'})

    # -- documents ---------------------------------------------------------
    def pdf_doc(doc_id: str) -> dict[str, Any]:
        doc = engine.get_doc(doc_id)
        if doc["kind"] != "pdf":
            raise HTTPException(422, "Readpath checks PDFs only (Office files are listed for now).")
        if not engine.current_path(doc_id).exists():
            raise HTTPException(409, doc["error"] or "This file hasn't been downloaded yet.")
        return doc

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: str):
        return report_of(engine.get_doc(doc_id))

    @app.post("/api/documents/{doc_id}/autofix")
    def autofix_document(doc_id: str):
        pdf_doc(doc_id)
        try:
            return report_of(engine.autofix_document(doc_id))
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.post("/api/documents/{doc_id}/recheck")
    def recheck_document(doc_id: str):
        pdf_doc(doc_id)
        return report_of(engine.check_document(doc_id, run_verapdf=True))

    @app.post("/api/documents/{doc_id}/revert")
    def revert_document(doc_id: str):
        pdf_doc(doc_id)
        return report_of(engine.revert_document(doc_id))

    @app.get("/api/documents/{doc_id}/download")
    def download_document(doc_id: str, version: str = Query("current", pattern="^(current|original)$")):
        doc = pdf_doc(doc_id)
        path = engine.current_path(doc_id) if version == "current" else engine.original_path(doc_id)
        stem = Path(doc["filename"]).stem
        name = f"{stem}-accessible.pdf" if version == "current" and doc["fixed"] else doc["filename"]
        return FileResponse(path, media_type="application/pdf", filename=name)

    def _png(request: Request, doc: dict[str, Any], key: str, make) -> Response:
        etag = f'"{doc["id"]}-{doc["revision"]}-{key}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        cache_dir = engine.cache / doc["id"] / str(doc["revision"])
        cache_file = cache_dir / f"{key}.png"
        if cache_file.exists():
            data = cache_file.read_bytes()
        else:
            data = make()
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, cache_file)
        return Response(data, media_type="image/png", headers={"ETag": etag, "Cache-Control": "no-cache"})

    @app.get("/api/documents/{doc_id}/pages/{n}/image")
    def page_image(request: Request, doc_id: str, n: int, width: int = Query(900, ge=100, le=2400)):
        doc = pdf_doc(doc_id)
        if not 1 <= n <= (doc["page_count"] or 10**6):
            raise HTTPException(404, "That page doesn't exist.")
        path = engine.current_path(doc_id)
        return _png(request, doc, f"p{n}-w{width}", lambda: render.page_png(path, n - 1, width))

    # -- review ------------------------------------------------------------
    def overview(doc_id: str) -> dict[str, Any]:
        with engine.reading(doc_id) as (doc, review):
            ov = review.overview(doc["filename"])
        return {"document_id": doc_id, "revision": doc["revision"], "vision_available": vision.available(), **ov}

    def page_blocks(doc_id: str, n: int) -> dict[str, Any]:
        with engine.reading(doc_id) as (doc, review):
            return {"revision": doc["revision"], "page": n, "blocks": review.page_blocks(n - 1)}

    def figures(doc_id: str) -> dict[str, Any]:
        with engine.reading(doc_id) as (doc, review):
            figs = review.figures()
        for f in figs:
            f["image_url"] = f"/api/documents/{doc_id}/figures/{f['id']}/image?rev={doc['revision']}"
        return {"revision": doc["revision"], "figures": figs}

    def tables(doc_id: str) -> dict[str, Any]:
        with engine.reading(doc_id) as (doc, review):
            tbls = review.tables()
        for t in tbls:
            t["image_url"] = f"/api/documents/{doc_id}/tables/{t['id']}/image?rev={doc['revision']}"
        return {"revision": doc["revision"], "tables": tbls}

    @app.get("/api/documents/{doc_id}/review")
    def get_review(doc_id: str):
        pdf_doc(doc_id)
        return overview(doc_id)

    @app.put("/api/documents/{doc_id}/metadata")
    def put_metadata(doc_id: str, body: MetadataBody):
        pdf_doc(doc_id)

        def fn(review):
            review.set_metadata(body.title, body.lang)
        engine.edit(doc_id, body.revision, fn)
        return overview(doc_id)

    @app.post("/api/documents/{doc_id}/review/{step}/complete")
    def complete_step(doc_id: str, step: str, body: Revisioned):
        pdf_doc(doc_id)
        if step not in STEP_IDS:
            raise HTTPException(404, "Unknown review step.")
        engine.edit(doc_id, body.revision, lambda review: review.complete_step(step))
        return overview(doc_id)

    @app.get("/api/documents/{doc_id}/pages/{n}/blocks")
    def get_blocks(doc_id: str, n: int):
        pdf_doc(doc_id)
        return page_blocks(doc_id, n)

    @app.put("/api/documents/{doc_id}/pages/{n}/order")
    def put_order(doc_id: str, n: int, body: OrderBody):
        pdf_doc(doc_id)
        engine.edit(doc_id, body.revision, lambda review: review.reorder_page(n - 1, body.block_ids))
        return page_blocks(doc_id, n)

    @app.put("/api/documents/{doc_id}/pages/{n}/blocks/{block_id}")
    def put_block(doc_id: str, n: int, block_id: str, body: BlockBody):
        pdf_doc(doc_id)
        engine.edit(doc_id, body.revision, lambda review: review.set_block_type(block_id, body.type))
        return page_blocks(doc_id, n)

    @app.get("/api/documents/{doc_id}/figures")
    def get_figures(doc_id: str):
        pdf_doc(doc_id)
        return figures(doc_id)

    @app.put("/api/documents/{doc_id}/figures/{fid}")
    def put_figure(doc_id: str, fid: str, body: FigureBody):
        pdf_doc(doc_id)
        engine.edit(doc_id, body.revision, lambda review: review.set_figure(fid, body.alt, body.decorative))
        return figures(doc_id)

    def _find(items: list[dict], item_id: str, what: str) -> dict:
        for it in items:
            if it["id"] == item_id:
                return it
        raise HTTPException(404, f"That {what} no longer exists. Reload to see the latest version.")

    @app.get("/api/documents/{doc_id}/figures/{fid}/image")
    def figure_image(request: Request, doc_id: str, fid: str):
        doc = pdf_doc(doc_id)
        fig = _find(figures(doc_id)["figures"], fid, "image")
        path = engine.current_path(doc_id)
        return _png(request, doc, f"fig-{fid}",
                    lambda: render.region_png(path, fig["page"] - 1, fig["bbox"], width=600))

    @app.post("/api/documents/{doc_id}/figures/{fid}/suggest")
    def suggest_alt(doc_id: str, fid: str):
        pdf_doc(doc_id)
        if not vision.available():
            raise HTTPException(503, "No image-description model is set up on this server.")
        fig = _find(figures(doc_id)["figures"], fid, "image")
        png = render.region_png(engine.current_path(doc_id), fig["page"] - 1, fig["bbox"], width=768)
        try:
            return {"alt": vision.suggest_alt_text(png, fig.get("context", ""))}
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/documents/{doc_id}/tables")
    def get_tables(doc_id: str):
        pdf_doc(doc_id)
        return tables(doc_id)

    @app.put("/api/documents/{doc_id}/tables/{tid}")
    def put_table(doc_id: str, tid: str, body: TableBody):
        pdf_doc(doc_id)
        engine.edit(doc_id, body.revision,
                    lambda review: review.set_table_headers(tid, body.header_row, body.header_column))
        return tables(doc_id)

    @app.get("/api/documents/{doc_id}/tables/{tid}/image")
    def table_image(request: Request, doc_id: str, tid: str):
        doc = pdf_doc(doc_id)
        tbl = _find(tables(doc_id)["tables"], tid, "table")
        path = engine.current_path(doc_id)
        return _png(request, doc, f"tbl-{tid}",
                    lambda: render.region_png(path, tbl["page"] - 1, tbl["bbox"], width=800))

    @app.post("/api/documents/{doc_id}/finish")
    def finish(doc_id: str, body: Revisioned):
        pdf_doc(doc_id)
        return report_of(engine.finish_review(doc_id, body.revision))

    # -- web app -------------------------------------------------------------
    web_dir = settings.web_dir

    @app.get("/api/{rest:path}")
    def api_404(rest: str):
        raise HTTPException(404, "Not found.")

    @app.get("/{rest:path}")
    def spa(rest: str):
        index = web_dir / "index.html"
        if rest:
            candidate = (web_dir / rest).resolve()
            if candidate.is_file() and web_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return Response("<h1>Readpath API is running</h1><p>The web app isn't built. Run "
                        "<code>npm run build</code> in <code>web/</code>.</p>", media_type="text/html")

    return app
