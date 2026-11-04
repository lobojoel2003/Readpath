"""Tests for the website crawler. No network: every request goes to httpx.MockTransport."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import itertools
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from readpath_server.crawler import (
    Crawler,
    CrawlSettings,
    CrawlStats,
    DownloadError,
    FoundDocument,
    _filename_for,
    document_kind,
    download,
    normalize_url,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x00" * 200
LEGACY_DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

Route = Callable[[httpx.Request], httpx.Response]


# --------------------------------------------------------------------------
# Fake website helpers
# --------------------------------------------------------------------------


def page(body: str) -> Route:
    return lambda request: httpx.Response(200, html=body)


def links(*hrefs: str) -> Route:
    anchors = "\n".join(f'<a href="{href}">link</a>' for href in hrefs)
    return page(f"<html><body>{anchors}</body></html>")


def redirect(location: str, status: int = 301) -> Route:
    return lambda request: httpx.Response(status, headers={"Location": location})


def file(content: bytes, content_type: str | None, disposition: str | None = None) -> Route:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if disposition:
        headers["Content-Disposition"] = disposition
    return lambda request: httpx.Response(200, content=content, headers=headers)


def text(body: str, content_type: str = "text/plain") -> Route:
    return file(body.encode(), content_type)


def status(code: int) -> Route:
    return lambda request: httpx.Response(code, html=f"<h1>Error {code}</h1>")


def fail(exc_type: type[httpx.TransportError]) -> Route:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated failure", request=request)

    return handler


class FakeSite:
    """Serves routes keyed by full URL; anything else is a 404 HTML page."""

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.requests: list[str] = []
        self.user_agents: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.user_agents.append(request.headers.get("user-agent", ""))
        route = self.routes.get(url)
        if route is None:
            return httpx.Response(404, html="<h1>Page not found</h1>")
        return route(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def requested(self, prefix: str) -> bool:
        return any(url.startswith(prefix) for url in self.requests)


@dataclass
class CrawlResult:
    stats: CrawlStats
    site: FakeSite
    pages: list[tuple[str, int]] = field(default_factory=list)
    docs: list[FoundDocument] = field(default_factory=list)

    @property
    def page_urls(self) -> list[str]:
        return [url for url, _ in self.pages]

    @property
    def doc_urls(self) -> list[str]:
        return [doc.url for doc in self.docs]

    def doc(self, url: str) -> FoundDocument:
        matches = [doc for doc in self.docs if doc.url == url]
        assert len(matches) == 1, f"{url} reported {len(matches)} times"
        return matches[0]


def run_crawl(
    start: str,
    routes: dict[str, Route],
    should_stop: Callable[[], bool] | None = None,
    result: CrawlResult | None = None,
    **overrides: object,
) -> CrawlResult:
    """Crawl a fake site with a sync on_page and an async on_document callback."""
    site = FakeSite(routes)
    result = result or CrawlResult(CrawlStats(), site)
    result.site = site
    settings = CrawlSettings(delay_seconds=0, **overrides)  # type: ignore[arg-type]

    def on_page(url: str, count: int) -> None:
        result.pages.append((url, count))

    async def on_document(doc: FoundDocument) -> None:
        await asyncio.sleep(0)
        result.docs.append(doc)

    async def main() -> CrawlStats:
        async with site.client() as client:
            crawler = Crawler(start, settings, client=client)
            stats = await crawler.run(
                on_page=on_page, on_document=on_document, should_stop=should_stop
            )
            assert not client.is_closed  # a client passed in is left open for the caller
            return stats

    result.stats = asyncio.run(main())
    return result


# --------------------------------------------------------------------------
# A small town website
# --------------------------------------------------------------------------

WWW = "https://www.exampletown.gov"
NEWS = f"{WWW}/news/"

ROBOTS = """User-agent: *
Disallow: /private/

Sitemap: https://www.exampletown.gov/sitemap_index.xml
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.exampletown.gov/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.exampletown.gov/about</loc></url>
  <url><loc>https://www.exampletown.gov/hidden-page</loc></url>
  <url><loc>https://www.exampletown.gov/docs/sitemap-only.pdf</loc></url>
  <url><loc>https://www.exampletown.gov/docs/budget.pdf</loc></url>
  <url><loc><![CDATA[https://www.exampletown.gov/private/hidden.pdf]]></loc></url>
</urlset>
"""

HOME = """<!DOCTYPE html>
<html><head><title>Example Town</title></head><body>
<nav>
  <a href="/about">About</a>
  <a href="/about#team">Team</a>
  <a href="https://www.exampletown.gov/about">About again</a>
  <a href="/news/">News</a>
  <a href="/old-page">Old page</a>
  <a href="/missing">Broken link</a>
</nav>
<a href="/docs/budget.pdf">Budget</a>
<a href="/DocumentCenter/View/1234/Agenda">Agenda</a>
<a href="/files/policy.docx">Policy</a>
<a rel="nofollow" href="/docs/nofollow.pdf">Nofollow</a>
<a href="https://cdn.examplecdn.com/files/annual-report.pdf">Annual report</a>
<a href="https://othersite.org/page">Another site</a>
<a href="mailto:clerk@exampletown.gov">Email us</a>
<a href="tel:+15555550100">Call us</a>
<a href="javascript:void(0)">Menu</a>
<img src="/images/logo.png"><a href="/images/logo.png">Logo</a>
<a href="/data/export.csv">Data</a>
<a href="/private/secret.pdf">Secret</a>
<a href="/private/page">Private</a>
</body></html>
"""

NEWS_PAGE = """<html><head>
<base href="/archive/2026/">
<link rel="stylesheet" type="text/css" href="/style.css">
<link rel="alternate" type="application/pdf" href="/docs/print-version">
</head><body>
<a href="minutes.pdf">Minutes</a>
<iframe src="/embed/viewer.pdf"></iframe>
<embed src="flyer.pdf">
<object data="/docs/map.pdf"></object>
<map><area href="/docs/district-map.pdf" alt="District map"></map>
<a href="/docs/budget.pdf#page=3">Budget, page 3</a>
<a href="http://exampletown.gov/docs/budget.pdf">Budget over http</a>
<a href="/files/report%202026.xlsx">Report</a>
<a href="/files/slides.pptx">Slides</a>
</body></html>
"""


def town_routes() -> dict[str, Route]:
    return {
        "https://exampletown.gov/": redirect(f"{WWW}/"),
        "https://exampletown.gov/robots.txt": redirect(f"{WWW}/robots.txt"),
        f"{WWW}/robots.txt": text(ROBOTS),
        f"{WWW}/sitemap_index.xml": text(SITEMAP_INDEX, "application/xml"),
        f"{WWW}/sitemap-pages.xml": text(SITEMAP_PAGES, "application/xml"),
        f"{WWW}/": page(HOME),
        f"{WWW}/about": links(
            "/docs/budget.pdf", "/deep/level2", "/DocumentCenter/View/1234/Agenda"
        ),
        f"{WWW}/old-page": redirect(f"{WWW}/about", 302),
        NEWS: page(NEWS_PAGE),
        f"{WWW}/deep/level2": links("/deep/level3"),
        f"{WWW}/deep/level3": links("/"),
        f"{WWW}/hidden-page": links("/docs/hidden.pdf"),
        f"{WWW}/DocumentCenter/View/1234/Agenda": file(
            PDF, "application/pdf", 'inline; filename="Agenda 01-02-2026.pdf"'
        ),
        f"{WWW}/docs/print-version": file(PDF, "application/pdf"),
        "https://othersite.org/page": links("/other.pdf"),
    }


def test_crawls_town_site_and_finds_documents() -> None:
    result = run_crawl("exampletown.gov", town_routes())
    home = f"{WWW}/"

    expected = {
        f"{WWW}/docs/budget.pdf": (home, "pdf", "budget.pdf"),
        f"{WWW}/DocumentCenter/View/1234/Agenda": (home, "pdf", "Agenda 01-02-2026.pdf"),
        f"{WWW}/files/policy.docx": (home, "word", "policy.docx"),
        f"{WWW}/docs/nofollow.pdf": (home, "pdf", "nofollow.pdf"),
        "https://cdn.examplecdn.com/files/annual-report.pdf": (home, "pdf", "annual-report.pdf"),
        f"{WWW}/archive/2026/minutes.pdf": (NEWS, "pdf", "minutes.pdf"),
        f"{WWW}/archive/2026/flyer.pdf": (NEWS, "pdf", "flyer.pdf"),
        f"{WWW}/embed/viewer.pdf": (NEWS, "pdf", "viewer.pdf"),
        f"{WWW}/docs/map.pdf": (NEWS, "pdf", "map.pdf"),
        f"{WWW}/docs/district-map.pdf": (NEWS, "pdf", "district-map.pdf"),
        f"{WWW}/docs/print-version": (NEWS, "pdf", "print-version.pdf"),
        f"{WWW}/files/report%202026.xlsx": (NEWS, "excel", "report 2026.xlsx"),
        f"{WWW}/files/slides.pptx": (NEWS, "powerpoint", "slides.pptx"),
        f"{WWW}/docs/hidden.pdf": (f"{WWW}/hidden-page", "pdf", "hidden.pdf"),
        f"{WWW}/docs/sitemap-only.pdf": (None, "pdf", "sitemap-only.pdf"),
    }
    assert sorted(result.doc_urls) == sorted(expected)  # each reported exactly once
    for url, (found_on, kind, filename) in expected.items():
        doc = result.doc(url)
        assert (doc.found_on, doc.kind, doc.filename) == (found_on, kind, filename), url
    assert result.doc(f"{WWW}/DocumentCenter/View/1234/Agenda").content_type == "application/pdf"
    assert result.doc(f"{WWW}/docs/budget.pdf").content_type is None  # not fetched during crawl

    assert sorted(result.page_urls) == sorted(
        [
            home,
            f"{WWW}/about",
            NEWS,
            f"{WWW}/hidden-page",
            f"{WWW}/deep/level2",
            f"{WWW}/deep/level3",
        ]
    )
    assert [count for _, count in result.pages] == [1, 2, 3, 4, 5, 6]
    assert result.stats.pages_crawled == 6
    assert result.stats.documents_found == len(expected)

    site = result.site
    for prefix in (
        "https://othersite.org",  # off-site pages are never crawled
        "https://cdn.examplecdn.com",  # off-site documents are reported, not fetched
        f"{WWW}/private/",  # robots.txt
        f"{WWW}/images/",
        f"{WWW}/data/",
        f"{WWW}/docs/budget.pdf",  # documents known by extension are not fetched
        f"{WWW}/style.css",
    ):
        assert not site.requested(prefix), prefix
    assert site.requested(f"{WWW}/DocumentCenter/View/1234/Agenda")
    assert all(agent.startswith("ReadpathBot/") for agent in site.user_agents)

    assert result.stats.errors == [
        f"Not found (HTTP 404): {WWW}/missing",
        "Skipped 3 links that robots.txt asks crawlers not to visit",
    ]


def test_start_url_redirect_target_is_in_scope() -> None:
    routes = {
        "https://oldtown.org/": redirect("https://www.newtown.gov/home"),
        "https://www.newtown.gov/home": links("/about", "https://newtown.gov/contact"),
        "https://www.newtown.gov/about": links("/docs/a.pdf"),
        "https://newtown.gov/contact": links(),
    }
    result = run_crawl("oldtown.org", routes)
    assert sorted(result.page_urls) == [
        "https://newtown.gov/contact",
        "https://www.newtown.gov/about",
        "https://www.newtown.gov/home",
    ]
    assert result.doc_urls == ["https://www.newtown.gov/docs/a.pdf"]


@pytest.mark.parametrize("include_subdomains", [False, True])
def test_subdomains(include_subdomains: bool) -> None:
    routes = {
        f"{WWW}/": links("https://parks.exampletown.gov/", "https://exampletown.gov/about"),
        "https://exampletown.gov/about": links(),
        "https://parks.exampletown.gov/": links("/pool-rules.pdf", "/schedule"),
        "https://parks.exampletown.gov/schedule": file(PDF, "application/pdf"),
    }
    result = run_crawl(WWW, routes, include_subdomains=include_subdomains)
    if include_subdomains:
        assert result.stats.pages_crawled == 3
        assert sorted(result.doc_urls) == [
            "https://parks.exampletown.gov/pool-rules.pdf",
            "https://parks.exampletown.gov/schedule",
        ]
    else:  # www.exampletown.gov and exampletown.gov are always the same site
        assert result.page_urls == [f"{WWW}/", "https://exampletown.gov/about"]
        assert result.docs == []
        assert not result.site.requested("https://parks.")


@pytest.mark.parametrize("concurrency", [1, 4])
def test_max_pages(concurrency: int) -> None:
    routes = {f"{WWW}/": links(*(f"/p{i}" for i in range(1, 6)))}
    for i in range(1, 6):
        routes[f"{WWW}/p{i}"] = links(f"/docs/p{i}.pdf")
    result = run_crawl(WWW, routes, max_pages=3, concurrency=concurrency)
    assert result.stats.pages_crawled == 3
    assert [count for _, count in result.pages] == [1, 2, 3]
    assert len(result.docs) == 2
    if concurrency == 1:
        assert result.page_urls == [f"{WWW}/", f"{WWW}/p1", f"{WWW}/p2"]
        assert not result.site.requested(f"{WWW}/p3")


def test_max_depth() -> None:
    routes = {
        f"{WWW}/": links("/a", "/docs/home.pdf"),
        f"{WWW}/a": links("/b", "/docs/a.pdf"),
        f"{WWW}/b": links("/c", "/docs/b.pdf"),
        f"{WWW}/c": links("/docs/c.pdf"),
    }
    result = run_crawl(WWW, routes, max_depth=1)
    assert result.page_urls == [f"{WWW}/", f"{WWW}/a"]
    # Documents linked from the deepest crawled page are still reported.
    assert sorted(result.doc_urls) == [f"{WWW}/docs/a.pdf", f"{WWW}/docs/home.pdf"]
    assert not result.site.requested(f"{WWW}/b")


@pytest.mark.parametrize("limits", [{"max_pages": 1}, {"max_depth": 0}])
def test_cms_document_links_probed_after_page_limits(limits: dict[str, int]) -> None:
    routes = {
        f"{WWW}/": links("/about", "/DocumentCenter/View/55/Minutes"),
        f"{WWW}/about": links("/docs/about.pdf"),
        f"{WWW}/DocumentCenter/View/55/Minutes": file(PDF, "application/pdf"),
    }
    result = run_crawl(WWW, routes, **limits)
    assert result.page_urls == [f"{WWW}/"]
    assert result.doc_urls == [f"{WWW}/DocumentCenter/View/55/Minutes"]
    assert result.doc(f"{WWW}/DocumentCenter/View/55/Minutes").filename == "Minutes.pdf"
    assert not result.site.requested(f"{WWW}/about")


def test_should_stop() -> None:
    routes = {
        f"{WWW}/": links("/p1", "/p2", "/docs/a.pdf"),
        f"{WWW}/p1": links(),
        f"{WWW}/p2": links(),
    }
    result = CrawlResult(CrawlStats(), FakeSite({}))
    run_crawl(WWW, routes, should_stop=lambda: len(result.pages) >= 1, result=result)
    assert result.stats.pages_crawled == 1
    assert result.docs == []
    assert not result.site.requested(f"{WWW}/p1")


def test_sync_document_and_async_page_callbacks() -> None:
    routes = {
        f"{WWW}/": links("/docs/a.pdf", "/docs/a.pdf#x", "/about"),
        f"{WWW}/about": links("/docs/a.pdf"),
    }
    pages: list[tuple[str, int]] = []
    docs: list[FoundDocument] = []

    async def on_page(url: str, count: int) -> None:
        await asyncio.sleep(0)
        pages.append((url, count))

    async def main() -> CrawlStats:
        async with FakeSite(routes).client() as client:
            crawler = Crawler(WWW, CrawlSettings(delay_seconds=0), client=client)
            return await crawler.run(on_page=on_page, on_document=docs.append)

    stats = asyncio.run(main())
    assert pages == [(f"{WWW}/", 1), (f"{WWW}/about", 2)]
    assert [doc.url for doc in docs] == [f"{WWW}/docs/a.pdf"]
    assert stats.documents_found == 1


def test_callback_errors_propagate() -> None:
    def on_document(doc: FoundDocument) -> None:
        raise RuntimeError("database is down")

    async def main() -> None:
        async with FakeSite({f"{WWW}/": links("/a.pdf")}).client() as client:
            await Crawler(WWW, CrawlSettings(delay_seconds=0), client=client).run(
                on_document=on_document
            )

    with pytest.raises(RuntimeError, match="database is down"):
        asyncio.run(main())


def test_per_url_failures_are_recorded_not_raised() -> None:
    routes = {
        f"{WWW}/": links("/broken", "/slow", "/server-error", "/ok"),
        f"{WWW}/broken": fail(httpx.ConnectError),
        f"{WWW}/slow": fail(httpx.ReadTimeout),
        f"{WWW}/server-error": status(500),
        f"{WWW}/ok": links("/docs/ok.pdf"),
    }
    result = run_crawl(WWW, routes)
    assert result.doc_urls == [f"{WWW}/docs/ok.pdf"]
    assert sorted(result.stats.errors) == sorted(
        [
            f"Could not load {WWW}/broken: could not connect to the website",
            f"Could not load {WWW}/slow: the website took too long to respond",
            f"Website error (HTTP 500): {WWW}/server-error",
        ]
    )


def test_errors_are_capped() -> None:
    routes = {f"{WWW}/": links(*(f"/missing-{i}" for i in range(60)))}
    result = run_crawl(WWW, routes)
    assert len(result.stats.errors) == 50
    assert result.stats.errors[-1] == "...and 11 more problems"


def test_redirects_for_documents_and_pages() -> None:
    signed = "https://bucket.s3.amazonaws.com/minutes.pdf?X-Amz-Signature=abc"
    routes = {
        f"{WWW}/": links("/DocumentCenter/View/77", "/download/88", "/external"),
        f"{WWW}/DocumentCenter/View/77": redirect(signed, 302),
        signed: file(PDF, "application/pdf"),
        f"{WWW}/download/88": redirect(f"{WWW}/files/final-report.pdf", 302),
        f"{WWW}/files/final-report.pdf": file(PDF, "application/octet-stream"),
        f"{WWW}/external": redirect("https://othersite.org/page"),
        "https://othersite.org/page": links("/other.pdf"),
    }
    result = run_crawl(WWW, routes)
    # Off-site redirect: keep the site's own (stable) link; in-site: the final URL.
    assert sorted(result.doc_urls) == [
        f"{WWW}/DocumentCenter/View/77",
        f"{WWW}/files/final-report.pdf",
    ]
    assert result.doc(f"{WWW}/DocumentCenter/View/77").filename == "minutes.pdf"
    assert result.page_urls == [f"{WWW}/"]  # the off-site page was not parsed


def test_probing_a_document_does_not_download_it() -> None:
    chunks_sent: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for i in range(50):
            chunks_sent.append(i)
            yield PDF + b"x" * 65536

    routes = {
        f"{WWW}/": links("/DocumentCenter/View/9"),
        f"{WWW}/DocumentCenter/View/9": lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "attachment; filename=Plan.PDF",
            },
            content=body(),
        ),
    }
    result = run_crawl(WWW, routes)
    doc = result.doc(f"{WWW}/DocumentCenter/View/9")
    assert (doc.kind, doc.filename, doc.content_type) == (
        "pdf",
        "Plan.PDF",
        "application/octet-stream",
    )
    assert len(chunks_sent) <= 1


def test_meta_refresh_and_frames() -> None:
    routes = {
        f"{WWW}/": page(
            '<html><head><meta http-equiv="refresh" content="0; URL=\'/home.aspx\'"></head></html>'
        ),
        f"{WWW}/home.aspx": page('<frameset><frame src="/menu.html"></frameset>'),
        f"{WWW}/menu.html": links("agenda.pdf"),
    }
    result = run_crawl(WWW, routes)
    assert result.page_urls == [f"{WWW}/", f"{WWW}/home.aspx", f"{WWW}/menu.html"]
    assert result.doc_urls == [f"{WWW}/agenda.pdf"]


def test_page_without_content_type_is_sniffed() -> None:
    routes = {
        f"{WWW}/": links("/plain", "/blob"),
        f"{WWW}/plain": file(b"<html><a href='/docs/x.pdf'>x</a></html>", None),
        f"{WWW}/blob": file(b"\x00\x01binary", None),
    }
    result = run_crawl(WWW, routes)
    assert result.page_urls == [f"{WWW}/", f"{WWW}/plain"]
    assert result.doc_urls == [f"{WWW}/docs/x.pdf"]


# --------------------------------------------------------------------------
# robots.txt and sitemaps
# --------------------------------------------------------------------------


def test_robots_disallowing_everything_blocks_start_page() -> None:
    routes = {f"{WWW}/robots.txt": text("User-agent: *\nDisallow: /\n"), f"{WWW}/": links("/a.pdf")}
    result = run_crawl(WWW, routes)
    assert result.stats.pages_crawled == 0
    assert result.docs == []
    assert result.stats.errors == [f"robots.txt asks crawlers not to visit {WWW}/"]
    assert not result.site.requested(f"{WWW}/sitemap.xml")

    ignored = run_crawl(WWW, routes, respect_robots=False)
    assert ignored.stats.pages_crawled == 1
    assert ignored.doc_urls == [f"{WWW}/a.pdf"]


def test_robots_rules_for_our_user_agent() -> None:
    robots = "User-agent: ReadpathBot\nDisallow: /reports/\n\nUser-agent: *\nDisallow: /\n"
    routes = {
        f"{WWW}/robots.txt": text(robots),
        f"{WWW}/": links("/reports/", "/reports/annual.pdf", "/about"),
        f"{WWW}/about": links(),
        f"{WWW}/reports/": links(),
    }
    result = run_crawl(WWW, routes)
    assert result.page_urls == [f"{WWW}/", f"{WWW}/about"]
    assert result.docs == []
    assert not result.site.requested(f"{WWW}/reports/")


@pytest.mark.parametrize(
    "robots_route",
    [
        status(500),
        status(403),
        fail(httpx.ConnectError),
        page("<html><body>Welcome! Disallow: /</body></html>"),
    ],
)
def test_unavailable_robots_allows_everything(robots_route: Route) -> None:
    routes = {
        f"{WWW}/robots.txt": robots_route,
        f"{WWW}/": links("/about"),
        f"{WWW}/about": links(),
    }
    result = run_crawl(WWW, routes)
    assert result.stats.pages_crawled == 2
    assert result.stats.errors == []


def test_gzip_sitemap_and_sitemap_limits() -> None:
    entries = "".join(f"<url><loc>{WWW}/page-{i}</loc></url>" for i in range(100))
    sitemap = gzip.compress(
        f"<urlset>{entries}<url><loc>{WWW}/docs/x.pdf</loc></url></urlset>".encode()
    )
    routes = {
        f"{WWW}/robots.txt": text(f"Sitemap: {WWW}/sitemap.xml.gz\n"),
        f"{WWW}/sitemap.xml.gz": file(sitemap, "application/x-gzip"),
        f"{WWW}/": links(),
    }
    for i in range(100):
        routes[f"{WWW}/page-{i}"] = links()
    result = run_crawl(WWW, routes, max_pages=5)
    assert result.stats.pages_crawled == 5
    assert f"{WWW}/page-0" in result.page_urls
    # Only 10 times max_pages sitemap entries are read, so the PDF at the end is not reached.
    assert result.docs == []

    result = run_crawl(WWW, routes, max_pages=20)
    assert result.docs[0].url == f"{WWW}/docs/x.pdf" and result.docs[0].found_on is None


def test_sitemaps_can_be_disabled() -> None:
    routes = {
        f"{WWW}/": links(),
        f"{WWW}/sitemap.xml": text(f"<urlset><url><loc>{WWW}/a.pdf</loc></url></urlset>"),
    }
    assert run_crawl(WWW, routes).doc_urls == [f"{WWW}/a.pdf"]
    result = run_crawl(WWW, routes, use_sitemaps=False)
    assert result.docs == []
    assert not result.site.requested(f"{WWW}/sitemap.xml")


# --------------------------------------------------------------------------
# Crawler set-up
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("exampletown.gov", "https://exampletown.gov/"),
        ("  www.ExampleTown.gov/Home  ", "https://www.exampletown.gov/Home"),
        ("http://exampletown.gov", "http://exampletown.gov/"),
        ("//exampletown.gov/x", "https://exampletown.gov/x"),
        ("localhost:8080", "https://localhost:8080/"),
    ],
)
def test_start_url_is_normalized(typed: str, expected: str) -> None:
    assert Crawler(typed).start_url == expected


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "mailto:clerk@exampletown.gov", "ftp://exampletown.gov", "javascript:alert(1)",
     "https://", "not a website", "http://exa mple.gov", "exampletown"],
)  # fmt: skip
def test_invalid_start_url(typed: str) -> None:
    with pytest.raises(ValueError):
        Crawler(typed)


def test_crawler_creates_and_closes_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    site = FakeSite({f"{WWW}/": links("/a.pdf")})
    created: list[tuple[httpx.AsyncClient, dict[str, object]]] = []
    real_client = httpx.AsyncClient

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        client = real_client(transport=httpx.MockTransport(site.handler), **kwargs)  # type: ignore[arg-type]
        created.append((client, kwargs))
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    stats = asyncio.run(Crawler(WWW, CrawlSettings(delay_seconds=0, timeout_seconds=7)).run())
    assert stats.documents_found == 1
    client, kwargs = created[0]
    assert client.is_closed
    assert kwargs["follow_redirects"] is True and kwargs["timeout"] == 7


def test_politeness_delay_spaces_requests() -> None:
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(asyncio.get_running_loop().time())
        if request.url.path == "/":
            return httpx.Response(200, html="<a href='/a'>a</a><a href='/b'>b</a>")
        return httpx.Response(200, html="<p>page</p>")

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            settings = CrawlSettings(delay_seconds=0.05, use_sitemaps=False)
            await Crawler(WWW, settings, client=client).run()

    asyncio.run(main())
    assert len(times) == 4  # robots.txt, /, /a, /b
    gaps = [later - earlier for earlier, later in itertools.pairwise(times)]
    assert min(gaps) >= 0.04


def test_should_stop_interrupts_politeness_delay() -> None:
    site = FakeSite({f"{WWW}/": links("/a")})

    async def main() -> CrawlStats:
        async with site.client() as client:
            crawler = Crawler(WWW, CrawlSettings(delay_seconds=30), client=client)
            return await crawler.run(should_stop=lambda: len(site.requests) >= 1)

    started = time.monotonic()
    stats = asyncio.run(main())
    assert time.monotonic() - started < 2
    assert stats.pages_crawled == 0
    assert site.requests == [f"{WWW}/robots.txt"]


# --------------------------------------------------------------------------
# normalize_url, document_kind, file names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "base", "expected"),
    [
        (
            "HTTPS://WWW.Example.GOV:443/a/../b/./c.pdf#frag",
            None,
            "https://www.example.gov/b/c.pdf",
        ),
        ("http://example.gov:80", None, "http://example.gov/"),
        ("http://example.gov:8080/x?b=2&a=1", None, "http://example.gov:8080/x?b=2&a=1"),
        ("../docs/a b.pdf", "https://x.gov/dir/page.html", "https://x.gov/docs/a%20b.pdf"),
        ("//cdn.example.com/x.pdf", "https://x.gov/", "https://cdn.example.com/x.pdf"),
        ("?page=2", "https://x.gov/list?page=1", "https://x.gov/list?page=2"),
        ("#top", "https://x.gov/a", "https://x.gov/a"),
        ("  /a.pdf\n", "https://x.gov/dir/", "https://x.gov/a.pdf"),
        ("https://x.gov/a%20b.pdf", None, "https://x.gov/a%20b.pdf"),
        ("https://x.gov/100%.pdf", None, "https://x.gov/100%25.pdf"),
        ("https://x.gov/café.pdf?q=é", None, "https://x.gov/caf%C3%A9.pdf?q=%C3%A9"),
        ("https://münchen.de/", None, "https://xn--mnchen-3ya.de/"),
        ("https://user:secret@x.gov/p", None, "https://x.gov/p"),
        ("https://[::1]:8443/x", None, "https://[::1]:8443/x"),
        ("https://x.gov", None, "https://x.gov/"),
        ("https://x.gov/?", None, "https://x.gov/"),
    ],
)
def test_normalize_url(url: str, base: str | None, expected: str) -> None:
    assert normalize_url(url, base) == expected


@pytest.mark.parametrize(
    "url",
    ["mailto:clerk@x.gov", "tel:+15555550100", "javascript:void(0)", "JavaScript:alert(1)",
     "data:text/html,hi", "ftp://x.gov/a.pdf", "", "   ", "http://x.gov:99999/",
     "https://exa mple.gov/"],
)  # fmt: skip
def test_normalize_url_rejects_non_web_urls(url: str) -> None:
    assert normalize_url(url, "https://x.gov/") is None
    assert normalize_url(url) is None


def test_normalize_url_needs_a_host() -> None:
    assert normalize_url("https://") is None
    assert normalize_url("/relative/path") is None


@pytest.mark.parametrize(
    ("url", "content_type", "disposition", "expected"),
    [
        ("https://x.gov/a.pdf", None, None, "pdf"),
        ("https://x.gov/A.PDF", None, None, "pdf"),
        ("https://x.gov/a.pdf;jsessionid=1", None, None, "pdf"),
        ("https://x.gov/a.docx", None, None, "word"),
        ("https://x.gov/a.rtf", None, None, "word"),
        ("https://x.gov/a.odt", None, None, "word"),
        ("https://x.gov/a.xlsm", None, None, "excel"),
        ("https://x.gov/a.ods", None, None, "excel"),
        ("https://x.gov/a.ppsx", None, None, "powerpoint"),
        ("https://x.gov/a.odp", None, None, "powerpoint"),
        ("https://x.gov/a.csv", None, None, None),
        ("https://x.gov/a.png", None, None, None),
        ("https://x.gov/about", None, None, None),
        ("https://x.gov/page.aspx", None, None, None),
        ("https://x.gov/View/1", "application/pdf", None, "pdf"),
        ("https://x.gov/View/1", "Application/X-PDF; charset=binary", None, "pdf"),
        ("https://x.gov/View/1", "application/msword", None, "word"),
        ("https://x.gov/View/1", DOCX_TYPE, None, "word"),
        ("https://x.gov/View/1", "application/vnd.ms-excel.sheet.macroEnabled.12", None, "excel"),
        ("https://x.gov/View/1", "application/vnd.ms-powerpoint", None, "powerpoint"),
        ("https://x.gov/View/1", "application/vnd.oasis.opendocument.spreadsheet", None, "excel"),
        ("https://x.gov/View/1", "application/vnd.oasis.opendocument.graphics", None, "other"),
        (
            "https://x.gov/View/1",
            "application/octet-stream",
            'attachment; filename="A.docx"',
            "word",
        ),
        (
            "https://x.gov/View/1",
            None,
            "attachment; filename*=UTF-8''Agenda%20%E2%80%93%201.pdf",
            "pdf",
        ),
        ("https://x.gov/a.pdf", "application/octet-stream", None, "pdf"),
        ("https://x.gov/View/1", "application/octet-stream", None, None),
        (
            "https://x.gov/View/1",
            "application/vnd.ms-excel",
            "attachment; filename=data.csv",
            "excel",
        ),
        ("https://x.gov/View/1", "application/octet-stream", "attachment; filename=data.csv", None),
        ("https://x.gov/a.pdf", "text/html; charset=utf-8", None, None),  # error page at a .pdf URL
        ("https://x.gov/View/1", "image/jpeg", "attachment; filename=photo.pdf", None),
    ],
)
def test_document_kind(
    url: str, content_type: str | None, disposition: str | None, expected: str | None
) -> None:
    assert document_kind(url, content_type, disposition) == expected


@pytest.mark.parametrize(
    ("url", "kind", "disposition", "expected"),
    [
        ("https://x.gov/DocumentCenter/View/1234/Agenda", "pdf", None, "Agenda.pdf"),
        ("https://x.gov/AgendaCenter/ViewFile/Agenda/_01022026-123", "pdf", None,
         "_01022026-123.pdf"),
        ("https://x.gov/", "pdf", None, "document.pdf"),
        ("https://x.gov/View.aspx?id=1", "pdf", None, "View.pdf"),
        ("https://x.gov/files/Minutes%203.15.2024", "pdf", None, "Minutes 3.15.2024.pdf"),
        ("https://x.gov/files/report.doc", "word", None, "report.doc"),
        ("https://x.gov/files/thing", "word", None, "thing.docx"),
        ("https://x.gov/files/thing", "other", None, "thing"),
        ("https://x.gov/a", "pdf", 'attachment; filename="..\\..\\evil.pdf"', "evil.pdf"),
        ("https://x.gov/a", "pdf", 'attachment; filename="a/b/c.pdf"', "c.pdf"),
        ("https://x.gov/a", "pdf", 'attachment; filename="what?.pdf"', "what_.pdf"),
        ("https://x.gov/a", "pdf",
         "attachment; filename*=UTF-8''Budget%202026%20%E2%80%93%20Final.pdf",
         "Budget 2026 \u2013 Final.pdf"),
        ("https://x.gov/a", "pdf",
         "attachment; filename=\"old.pdf\"; filename*=UTF-8''new%C3%A9.pdf",
         "new\u00e9.pdf"),
        ("https://x.gov/a", "pdf", 'attachment; filename="   "', "a.pdf"),
    ],
)  # fmt: skip
def test_filename(url: str, kind: str, disposition: str | None, expected: str) -> None:
    assert _filename_for(url, kind, disposition) == expected


def test_long_filename_is_truncated_keeping_extension() -> None:
    name = _filename_for("https://x.gov/" + "a" * 300 + ".pdf", "pdf")
    assert len(name) == 150 and name.endswith(".pdf")


# --------------------------------------------------------------------------
# download()
# --------------------------------------------------------------------------


def run_download(
    routes: dict[str, Route], url: str, dest: Path, **kwargs: object
) -> tuple[tuple[str | None, int, str], FakeSite]:
    site = FakeSite(routes)

    async def main() -> tuple[str | None, int, str]:
        async with site.client() as client:
            return await download(client, url, str(dest), **kwargs)  # type: ignore[arg-type]

    return asyncio.run(main()), site


def test_download_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "scan" / "doc.pdf"
    routes = {
        f"{WWW}/DocumentCenter/View/1": redirect(f"{WWW}/files/agenda.pdf", 302),
        f"{WWW}/files/agenda.pdf": file(PDF, "application/pdf; qs=0.9"),
    }
    (content_type, size, sha256), site = run_download(
        routes, f"{WWW}/DocumentCenter/View/1", dest, user_agent="ReadpathBot/test"
    )
    assert (content_type, size, sha256) == (
        "application/pdf",
        len(PDF),
        hashlib.sha256(PDF).hexdigest(),
    )
    assert dest.read_bytes() == PDF
    assert [p.name for p in dest.parent.iterdir()] == ["doc.pdf"]  # no temp file left behind
    assert site.user_agents == ["ReadpathBot/test", "ReadpathBot/test"]


@pytest.mark.parametrize(
    ("content", "content_type", "url"),
    [
        (DOCX, DOCX_TYPE, f"{WWW}/files/a.docx"),
        (LEGACY_DOC, "application/msword", f"{WWW}/files/a.doc"),
        (b"\x00" * 500 + PDF, "application/pdf", f"{WWW}/files/junk-first.pdf"),
        (PDF, "text/html", f"{WWW}/files/misconfigured.pdf"),  # the bytes decide
        (DOCX, "application/octet-stream", f"{WWW}/DocumentCenter/View/3"),
    ],
)
def test_download_accepts_documents(
    tmp_path: Path, content: bytes, content_type: str, url: str
) -> None:
    (_, size, _), _ = run_download({url: file(content, content_type)}, url, tmp_path / "file")
    assert size == len(content)


@pytest.mark.parametrize(
    ("route", "message"),
    [
        (file(b"<!DOCTYPE html><html><body>Sorry</body></html>", "application/pdf"), "web page"),
        (file(b"\n  <html><body>Login</body></html>", "application/octet-stream"), "web page"),
        (file(b"GIF89a....", "application/pdf"), "not a PDF or Office document"),
        (file(b"", "application/pdf"), "empty"),
        (file(DOCX, "application/pdf"), "should be a PDF"),
        (status(404), r"not found \(HTTP 404\)"),
        (status(403), r"access denied \(HTTP 403\)"),
        (fail(httpx.ConnectError), "could not connect"),
        (fail(httpx.ReadTimeout), "took too long"),
    ],
)
def test_download_errors(tmp_path: Path, route: Route, message: str) -> None:
    dest = tmp_path / "doc.pdf"
    dest.write_bytes(b"previous version")
    url = f"{WWW}/files/doc.pdf"
    with pytest.raises(DownloadError, match=message):
        run_download({url: route}, url, dest)
    assert dest.read_bytes() == b"previous version"  # never replaced by a partial or bad file
    assert [p.name for p in tmp_path.iterdir()] == ["doc.pdf"]


def test_download_size_limit_from_content_length(tmp_path: Path) -> None:
    url = f"{WWW}/big.pdf"
    with pytest.raises(DownloadError, match="larger than the 1000 bytes limit"):
        run_download(
            {url: file(PDF + b"x" * 5000, "application/pdf")},
            url,
            tmp_path / "big.pdf",
            max_bytes=1000,
        )
    assert list(tmp_path.iterdir()) == []


def test_download_size_limit_while_streaming(tmp_path: Path) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield PDF
        for _ in range(20):
            yield b"x" * 1024 * 1024

    url = f"{WWW}/big.pdf"

    def route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=body())

    with pytest.raises(DownloadError, match="larger than the 5 MB limit"):
        run_download({url: route}, url, tmp_path / "big.pdf", max_bytes=5 * 1024 * 1024)
    assert list(tmp_path.iterdir()) == []
