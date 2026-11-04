# SPDX-License-Identifier: AGPL-3.0-or-later
"""Background work: crawling, downloading, checking and fixing documents.

Files live under ``<data>/files/<doc id>/`` as ``original.pdf`` (never
changed) and ``current.pdf`` (the working copy every fix and review edit
updates). Edits to one document are serialized with a per-document lock and
every change bumps the document's ``revision``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

import httpx
import pikepdf
from readpath_core import autofix_file, finalize_pdf, run_checks
from readpath_core import metadata as core_md
from readpath_core import ocr as core_ocr
from readpath_core import verapdf
from readpath_core.checks import ProblemOpening
from readpath_core.review import Review

from .config import Settings
from .crawler import Crawler, CrawlSettings, DownloadError, FoundDocument, download
from .db import Database, now

log = logging.getLogger("readpath")

TERMINAL_SCAN = ("done", "failed", "cancelled")


class Conflict(Exception):
    """The client edited an out-of-date revision."""


class NotFound(Exception):
    pass


class UnsafeURL(ValueError):
    pass


def check_public_url(url: str, allow_private: bool) -> None:
    """Refuse URLs that point at this server's private network (SSRF guard)."""
    if allow_private:
        return
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise UnsafeURL("That address has no host name.")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURL(f"Could not find the website “{host}”. Check the address.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURL("That address points to a private network, which this server doesn't scan. "
                            "Set READPATH_ALLOW_PRIVATE_HOSTS=1 to allow it.")


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.data_dir / "readpath.db")
        self.files = settings.data_dir / "files"
        self.cache = settings.data_dir / "cache"
        self.files.mkdir(exist_ok=True)
        self.cache.mkdir(exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="readpath-check")
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        for scan in self.db.list_scans():
            if scan["status"] in ("queued", "crawling"):
                self.db.update_scan(scan["id"], status="done", finished_at=now(),
                                    message="The scan stopped early because the server restarted.")
        for doc in self.db.documents_with_status(("downloading",)):
            self.db.update_document(doc["id"], status="error", error="The server restarted during download.")
        for doc in self.db.documents_with_status(("queued", "checking", "fixing")):
            if self.current_path(doc["id"]).exists():
                self.spawn(self._check_async(doc["id"]))
            else:
                self.db.update_document(doc["id"], status="error", error="The file was not downloaded.")
        for scan in self.db.list_scans():
            if scan["status"] in ("checking", "fixing"):
                self.spawn(self._finish_when_idle(scan["id"]))

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.db.close()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- paths and locks ---------------------------------------------------
    def doc_dir(self, doc_id: str) -> Path:
        return self.files / doc_id

    def original_path(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "original.pdf"

    def current_path(self, doc_id: str) -> Path:
        return self.doc_dir(doc_id) / "current.pdf"

    def lock(self, doc_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(doc_id, threading.RLock())

    def get_doc(self, doc_id: str) -> dict[str, Any]:
        doc = self.db.get_document(doc_id)
        if doc is None:
            raise NotFound("That document doesn't exist.")
        return doc

    def get_scan(self, scan_id: str) -> dict[str, Any]:
        scan = self.db.get_scan(scan_id)
        if scan is None:
            raise NotFound("That scan doesn't exist.")
        return scan

    # -- checking ----------------------------------------------------------
    def check_document(self, doc_id: str, *, run_verapdf: bool = False) -> dict[str, Any]:
        """Check the current version and store the report (runs in a worker thread)."""
        with self.lock(doc_id):
            doc = self.get_doc(doc_id)
            path = self.current_path(doc_id)
            self.db.update_document(doc_id, status="checking")
            try:
                with pikepdf.open(path) as pdf:
                    report = run_checks(pdf, filename=doc["filename"], ocr_available=core_ocr.ocr_available(),
                                        max_pages=self.settings.max_check_pages)
            except pikepdf.PasswordError:
                self.db.update_document(doc_id, status="error",
                                        error="This PDF is password-protected. Upload a copy without a password.")
                return self.get_doc(doc_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("check failed for %s: %s", doc_id, exc)
                msg = str(exc) if isinstance(exc, ProblemOpening) else "This file could not be opened as a PDF. It may be damaged."
                self.db.update_document(doc_id, status="error", error=msg)
                return self.get_doc(doc_id)
            values: dict[str, Any] = {
                "status": "checked",
                "error": None,
                "report": report.to_dict(),
                "score": report.score,
                "grade": report.grade,
                "counts": report.counts(),
                "page_count": report.page_count,
                "title": report.title,
                "checked_at": now(),
            }
            if doc["original_score"] is None:
                values["original_score"] = report.score
            if run_verapdf and verapdf.available():
                values["verapdf"] = verapdf.validate(path).to_dict()
            elif doc["verapdf"] is not None:
                values["verapdf"] = None  # stale after any change
            self.db.update_document(doc_id, **values)
            return self.get_doc(doc_id)

    async def _check_async(self, doc_id: str) -> None:
        assert self.loop is not None
        try:
            await self.loop.run_in_executor(
                self.pool, lambda: self.check_document(doc_id, run_verapdf=self.settings.verapdf_on_scan))
        except Exception:  # noqa: BLE001
            log.exception("check crashed for %s", doc_id)
            self.db.update_document(doc_id, status="error", error="Readpath hit a problem checking this file.")

    # -- fixing ------------------------------------------------------------
    def autofix_document(self, doc_id: str) -> dict[str, Any]:
        with self.lock(doc_id):
            doc = self.get_doc(doc_id)
            if doc["kind"] != "pdf" or not self.current_path(doc_id).exists():
                raise NotFound("Only PDFs that were downloaded can be fixed.")
            self.db.update_document(doc_id, status="fixing")
            report = (doc.get("report") or {})
            retag = any(c["id"] == "untagged-content" and c["fix"] == "auto" and c["status"] == "fail"
                        for c in report.get("checks", []))
            src = self.current_path(doc_id)
            fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=self.doc_dir(doc_id))
            os.close(fd)
            try:
                result = autofix_file(src, tmp, filename=doc["filename"], default_lang=self.settings.default_lang,
                                      retag=retag)
                os.replace(tmp, src)
            except Exception as exc:  # noqa: BLE001
                log.exception("autofix failed for %s", doc_id)
                Path(tmp).unlink(missing_ok=True)
                self.db.update_document(doc_id, status="checked")
                raise RuntimeError("Readpath couldn't fix this file automatically.") from exc
            fixes = result.applied + result.skipped
            if not fixes:
                fixes = ["Nothing needed fixing automatically."]
            self.db.update_document(doc_id, revision=doc["revision"] + 1, fixed=1, fixes=fixes)
            return self.check_document(doc_id)

    async def autofix_scan(self, scan_id: str) -> None:
        assert self.loop is not None
        self.db.update_scan(scan_id, status="fixing", message="Fixing documents automatically…",
                            finished_at=None)
        docs = [d for d in self.db.list_documents(scan_id)
                if d["kind"] == "pdf" and d["status"] == "checked" and d["grade"] == "auto_fixable"]
        for d in docs:
            self.db.update_document(d["id"], status="fixing")
        done = 0
        for d in docs:
            if self.db.get_scan(scan_id) is None:
                return
            try:
                await self.loop.run_in_executor(self.pool, self.autofix_document, d["id"])
            except Exception:  # noqa: BLE001
                self.db.update_document(d["id"], status="checked")
            done += 1
            self.db.update_scan(scan_id, message=f"Fixed {done} of {len(docs)} documents…")
        self.db.update_scan(scan_id, status="done", finished_at=now(),
                            message=f"Fixed {len(docs)} document{'s' if len(docs) != 1 else ''} automatically.")

    def revert_document(self, doc_id: str) -> dict[str, Any]:
        with self.lock(doc_id):
            doc = self.get_doc(doc_id)
            shutil.copyfile(self.original_path(doc_id), self.current_path(doc_id))
            self.db.update_document(doc_id, revision=doc["revision"] + 1, fixed=0, fixes=None)
            return self.check_document(doc_id)

    # -- review edits --------------------------------------------------------
    @contextmanager
    def reading(self, doc_id: str) -> Iterator[tuple[dict[str, Any], Review]]:
        doc = self.get_doc(doc_id)
        if doc["kind"] != "pdf" or not self.current_path(doc_id).exists():
            raise NotFound("This document has no PDF to review.")
        with pikepdf.open(self.current_path(doc_id)) as pdf:
            yield doc, Review(pdf)

    def edit(self, doc_id: str, revision: int, fn: Callable[[Review], Any]) -> tuple[Any, int]:
        """Apply an edit, save, re-check. Returns (fn result, new revision)."""
        with self.lock(doc_id):
            doc = self.get_doc(doc_id)
            if int(revision) != int(doc["revision"]):
                raise Conflict("This document changed since you loaded it. Reloading the latest version.")
            path = self.current_path(doc_id)
            with pikepdf.open(path) as pdf:
                review = Review(pdf)
                fn(review)
                fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=self.doc_dir(doc_id))
                os.close(fd)
                pdf.save(tmp)
            os.replace(tmp, path)
            new_rev = doc["revision"] + 1
            self.db.update_document(doc_id, revision=new_rev, fixed=1)
            self.check_document(doc_id)
            return None, new_rev

    def finish_review(self, doc_id: str, revision: int) -> dict[str, Any]:
        with self.lock(doc_id):
            doc = self.get_doc(doc_id)
            if int(revision) != int(doc["revision"]):
                raise Conflict("This document changed since you loaded it. Reloading the latest version.")
            path = self.current_path(doc_id)
            changed = False
            with pikepdf.open(path) as pdf:
                was = core_md.pdfua_marked(pdf)
                if finalize_pdf(pdf) and not was:
                    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=self.doc_dir(doc_id))
                    os.close(fd)
                    pdf.save(tmp)
                    changed = True
            if changed:
                os.replace(tmp, path)
                self.db.update_document(doc_id, revision=doc["revision"] + 1, fixed=1)
            return self.check_document(doc_id, run_verapdf=True)

    # -- scans -------------------------------------------------------------
    def create_upload_doc(self, scan_id: str, filename: str, data_path: Path, size: int) -> dict[str, Any]:
        doc = self.db.create_document(scan_id=scan_id, filename=filename, kind="pdf", status="queued",
                                      size_bytes=size)
        d = self.doc_dir(doc["id"])
        d.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data_path), self.original_path(doc["id"]))
        shutil.copyfile(self.original_path(doc["id"]), self.current_path(doc["id"]))
        return doc

    async def run_uploads(self, scan_id: str, doc_ids: list[str]) -> None:
        self.db.update_scan(scan_id, status="checking", message=f"Checking {len(doc_ids)} file(s)…")
        await asyncio.gather(*(self._check_async(d) for d in doc_ids))
        self._mark_done(scan_id)

    def _mark_done(self, scan_id: str) -> None:
        scan = self.db.get_scan(scan_id)
        if scan is None or scan["status"] == "cancelled":
            return
        summary = self.db.scan_summary(scan_id)
        parts = [f"Found {summary['pdfs']} PDF{'s' if summary['pdfs'] != 1 else ''}"]
        if scan["kind"] == "crawl":
            parts.append(f"on {scan['pages_crawled']} web page{'s' if scan['pages_crawled'] != 1 else ''}")
        message = " ".join(parts) + "."
        if summary["other_documents"]:
            message += f" Also found {summary['other_documents']} Office file(s), listed but not checked yet."
        self.db.update_scan(scan_id, status="done", finished_at=now(), message=message)

    async def _finish_when_idle(self, scan_id: str) -> None:
        while True:
            if self.db.get_scan(scan_id) is None:
                return
            if not self.db.scan_summary(scan_id)["pending"]:
                break
            await asyncio.sleep(1)
        self._mark_done(scan_id)

    async def run_crawl(self, scan_id: str) -> None:
        scan = self.get_scan(scan_id)
        settings = CrawlSettings(
            max_pages=scan["max_pages"] or 200,
            max_depth=scan["max_depth"] if scan["max_depth"] is not None else 5,
            include_subdomains=bool(scan["include_subdomains"]),
        )
        self.db.update_scan(scan_id, status="crawling", message="Starting the scan…")
        downloads: list[asyncio.Task] = []
        sem = asyncio.Semaphore(3)

        def stopped() -> bool:
            s = self.db.get_scan(scan_id)
            return s is None or bool(s["cancel_requested"])

        async def on_page(url: str, count: int) -> None:
            self.db.update_scan(scan_id, pages_crawled=count,
                                message=f"Visited {count} of up to {settings.max_pages} pages")

        async with httpx.AsyncClient(follow_redirects=True, timeout=settings.timeout_seconds,
                                     headers={"User-Agent": settings.user_agent}) as client:

            async def fetch(doc_id: str, url: str) -> None:
                async with sem:
                    if stopped():
                        self.db.update_document(doc_id, status="error", error="The scan was cancelled.")
                        return
                    self.db.update_document(doc_id, status="downloading")
                    try:
                        check_public_url(url, self.settings.allow_private_hosts)
                        self.doc_dir(doc_id).mkdir(parents=True, exist_ok=True)
                        ctype, size, sha = await download(client, url, str(self.original_path(doc_id)),
                                                          max_bytes=settings.max_document_bytes,
                                                          user_agent=settings.user_agent)
                    except (DownloadError, UnsafeURL) as exc:
                        self.db.update_document(doc_id, status="error", error=str(exc))
                        return
                    except Exception as exc:  # noqa: BLE001
                        self.db.update_document(doc_id, status="error", error=f"Download failed: {exc}")
                        return
                    shutil.copyfile(self.original_path(doc_id), self.current_path(doc_id))
                    self.db.update_document(doc_id, status="queued", size_bytes=size, sha256=sha)
                await self._check_async(doc_id)

            async def on_document(found: FoundDocument) -> None:
                if self.db.find_document(scan_id, found.url) is not None:
                    return
                is_pdf = found.kind == "pdf"
                doc = self.db.create_document(
                    scan_id=scan_id, filename=found.filename, kind=found.kind, url=found.url,
                    found_on=found.found_on, status="queued" if is_pdf else "not_checked")
                if is_pdf:
                    downloads.append(asyncio.ensure_future(fetch(doc["id"], found.url)))

            try:
                check_public_url(scan["start_url"], self.settings.allow_private_hosts)
                crawler = Crawler(scan["start_url"], settings, client=client)
                stats = await crawler.run(on_page=on_page, on_document=on_document, should_stop=stopped)
                self.db.update_scan(scan_id, pages_crawled=stats.pages_crawled, crawl_errors=stats.errors[:50])
            except (ValueError, UnsafeURL) as exc:
                self.db.update_scan(scan_id, status="failed", finished_at=now(), message=str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("crawl failed")
                self.db.update_scan(scan_id, status="failed", finished_at=now(),
                                    message=f"The scan failed: {exc}")
                return
            if stopped():
                for t in downloads:
                    t.cancel()
                for d in self.db.list_documents(scan_id):
                    if d["status"] in ("queued", "downloading") and not self.current_path(d["id"]).exists():
                        self.db.update_document(d["id"], status="error", error="The scan was cancelled.")
                return
            self.db.update_scan(scan_id, status="checking",
                                message=f"Checking the PDFs found on {stats.pages_crawled} pages…")
            await asyncio.gather(*downloads, return_exceptions=True)
        if not stopped():
            self._mark_done(scan_id)

    def delete_scan(self, scan_id: str) -> None:
        self.get_scan(scan_id)
        self.db.update_scan(scan_id, cancel_requested=1)
        for d in self.db.list_documents(scan_id):
            shutil.rmtree(self.doc_dir(d["id"]), ignore_errors=True)
            shutil.rmtree(self.cache / d["id"], ignore_errors=True)
        self.db.delete_scan(scan_id)
