"""Website crawler that finds the documents a website links to.

A clerk enters their site's address; :class:`Crawler` walks the site's HTML
pages breadth-first and reports every linked document (PDF first, plus Word,
Excel and PowerPoint files so they can be listed). :func:`download` then
fetches a document for checking.

Many small-government CMSs (CivicPlus and others) serve PDFs from URLs with no
extension, such as ``/DocumentCenter/View/1234/Agenda``, so detection uses the
response ``Content-Type`` and ``Content-Disposition`` headers as well as the
URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.message
import email.utils
import hashlib
import html
import inspect
import os
import re
import tempfile
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import BinaryIO
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

__all__ = [
    "CrawlSettings",
    "CrawlStats",
    "Crawler",
    "DownloadError",
    "FoundDocument",
    "document_kind",
    "download",
    "normalize_url",
]

# --------------------------------------------------------------------------
# Public data types
# --------------------------------------------------------------------------


@dataclass
class CrawlSettings:
    """Limits and politeness options for one crawl."""

    max_pages: int = 200  # HTML pages to fetch (documents don't count)
    max_depth: int = 5  # link hops from the start page
    include_subdomains: bool = False  # www.x.gov and x.gov are always the same site
    delay_seconds: float = 0.25  # politeness delay between requests
    timeout_seconds: float = 30.0
    max_document_bytes: int = 100 * 1024 * 1024
    user_agent: str = "ReadpathBot/0.1 (+https://github.com/lobojoel2003/readpath)"
    respect_robots: bool = True
    use_sitemaps: bool = True
    concurrency: int = 4


@dataclass
class FoundDocument:
    """A document linked from the website."""

    url: str  # final URL after redirects (fragment removed)
    found_on: str | None  # HTML page URL that linked to it (None if from sitemap)
    kind: str  # "pdf" | "word" | "excel" | "powerpoint" | "other"
    filename: str  # from Content-Disposition, else URL basename, else "document.pdf"
    content_type: str | None


@dataclass
class CrawlStats:
    """What a crawl did, for progress and the final summary."""

    pages_crawled: int = 0
    documents_found: int = 0
    errors: list[str] = field(default_factory=list)  # short, plain-language, capped


class DownloadError(Exception):
    """A document could not be downloaded; ``str(exc)`` is a plain-language reason."""


PageCallback = Callable[[str, int], Awaitable[None] | None]
DocumentCallback = Callable[[FoundDocument], Awaitable[None] | None]
StopCallback = Callable[[], bool]

# --------------------------------------------------------------------------
# Limits and lookup tables
# --------------------------------------------------------------------------

_MAX_ERRORS = 50
_MAX_PAGE_BYTES = 5 * 1024 * 1024
_MAX_ROBOTS_BYTES = 512 * 1024
_MAX_SITEMAP_BYTES = 20 * 1024 * 1024
_MAX_SITEMAP_FILES = 25
_SITEMAP_URLS_PER_PAGE = 10  # sitemap entries read = this times max_pages
_QUEUE_PER_PAGE = 20  # URLs queued per depth level = this times max_pages (min 1000)
_MAX_CRAWL_DELAY = 10.0  # cap on robots.txt Crawl-delay, in seconds
_MAX_URL_LENGTH = 2048
_MAX_FILENAME = 150
_SNIFF_BYTES = 1024
_CHUNK_SIZE = 64 * 1024

_ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"

_EXTENSION_KINDS: dict[str, str] = {
    "pdf": "pdf",
    "doc": "word", "docx": "word", "docm": "word", "rtf": "word", "odt": "word",
    "xls": "excel", "xlsx": "excel", "xlsm": "excel", "ods": "excel",
    "ppt": "powerpoint", "pptx": "powerpoint", "pps": "powerpoint",
    "ppsx": "powerpoint", "odp": "powerpoint",
}  # fmt: skip
_DEFAULT_EXTENSIONS = {"pdf": ".pdf", "word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx"}
_KIND_LABELS = {"pdf": "PDF", "word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}

# Links with these extensions are never documents and never pages worth fetching.
_SKIPPED_EXTENSIONS = frozenset({
    "jpg", "jpeg", "png", "gif", "bmp", "svg", "webp", "ico", "tif", "tiff", "heic",  # images
    "mp4", "m4v", "mov", "avi", "wmv", "webm", "mkv", "flv", "mpg", "mpeg",  # video
    "mp3", "wav", "m4a", "aac", "ogg", "oga", "wma", "flac",  # audio
    "zip", "rar", "7z", "gz", "tgz", "tar", "bz2", "exe", "msi", "dmg", "iso",  # archives
    "css", "js", "mjs", "map", "json", "xml", "rss", "atom", "csv", "txt", "ics", "vcf",  # data
    "woff", "woff2", "ttf", "otf", "eot",  # fonts
})  # fmt: skip
# Server-side page extensions that make a poor document filename ("View.aspx").
_WEB_PAGE_EXTENSIONS = frozenset({"htm", "html", "aspx", "asp", "ashx", "php", "jsp", "cfm", "cgi"})

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
# Content types that say nothing about the file, so the filename decides.
_GENERIC_TYPES = frozenset({
    "application/octet-stream", "binary/octet-stream", "application/force-download",
    "application/download", "application/x-download", "application/unknown",
    "application/x-unknown", "application/binary", "text/plain",
})  # fmt: skip
_EXACT_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/msword": "word",
    "application/rtf": "word",
    "text/rtf": "word",
}
_CONTENT_TYPE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("application/vnd.openxmlformats-officedocument.wordprocessingml", "word"),
    ("application/vnd.openxmlformats-officedocument.spreadsheetml", "excel"),
    ("application/vnd.openxmlformats-officedocument.presentationml", "powerpoint"),
    ("application/vnd.openxmlformats-officedocument.", "other"),
    ("application/vnd.oasis.opendocument.text", "word"),
    ("application/vnd.oasis.opendocument.spreadsheet", "excel"),
    ("application/vnd.oasis.opendocument.presentation", "powerpoint"),
    ("application/vnd.oasis.opendocument.", "other"),
    ("application/vnd.ms-word", "word"),
    ("application/vnd.ms-excel", "excel"),
    ("application/vnd.ms-powerpoint", "powerpoint"),
)

# Extensionless URLs that CMSs commonly use to serve files. These are still
# probed after the page budget or depth limit is reached, so documents linked
# from the last pages crawled are not lost.
_LIKELY_DOCUMENT_URL = re.compile(
    r"documentcenter/|viewfile|archive\.aspx\?adid=|showpublisheddocument|showdocument"
    r"|blobdload|getfile|electronicfile|/download\b",
    re.IGNORECASE,
)

_DEFAULT_PORTS = {"http": 80, "https": 443}
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_URL_JUNK_RE = re.compile(r"[\t\r\n]")
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_HOST_RE = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*")
_IPV6_RE = re.compile(r"[0-9a-f:.]+")
_PATH_SAFE = "/%!$&'()*+,;=:@-._~"
_QUERY_SAFE = _PATH_SAFE + "?[]"
_REFRESH_URL_RE = re.compile(r"url\s*=\s*['\"]?([^'\"]+)", re.IGNORECASE)
_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", re.IGNORECASE | re.DOTALL)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex[\s>]", re.IGNORECASE)
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"|?*]')

# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------


def normalize_url(url: str | None, base: str | None = None) -> str | None:
    """Return a canonical absolute http(s) URL, or ``None`` if it isn't one.

    Resolves ``url`` against ``base``, drops the fragment and any user:password,
    lowercases the scheme and host (IDNA-encoding non-ASCII hosts), drops
    default ports, removes ``.``/``..`` path segments and percent-encodes
    characters that are not allowed in URLs. The query string is kept as is.
    ``mailto:``, ``tel:``, ``javascript:``, ``data:`` and other schemes give
    ``None``.
    """
    if not url:
        return None
    text = _URL_JUNK_RE.sub("", url).strip()
    if not text:
        return None
    scheme = _SCHEME_RE.match(text)
    if scheme and scheme.group(1).lower() not in _DEFAULT_PORTS:
        return None
    try:
        if base:
            text = urljoin(base, text)
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme_name = parts.scheme.lower()
    if scheme_name not in _DEFAULT_PORTS:
        return None
    host = _normalize_host(parts.hostname)
    if host is None:
        return None
    netloc = host if port in (None, _DEFAULT_PORTS[scheme_name]) else f"{host}:{port}"
    path = _quote(_remove_dot_segments(parts.path or "/"), _PATH_SAFE)
    query = _quote(parts.query, _QUERY_SAFE)
    return f"{scheme_name}://{netloc}{path}" + (f"?{query}" if query else "")


def _normalize_host(hostname: str | None) -> str | None:
    """Lowercase, IDNA-encode and validate a host name (``None`` if invalid)."""
    if not hostname:
        return None
    host = hostname.rstrip(".").lower()
    if ":" in host:  # IPv6 literal (urlsplit already removed the brackets)
        return f"[{host}]" if _IPV6_RE.fullmatch(host) else None
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    return host if _HOST_RE.fullmatch(host) else None


def _remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` segments in an absolute path (RFC 3986 5.2.4)."""
    if "." not in path:
        return path
    segments = path.split("/")
    output: list[str] = []
    for segment in segments:
        if segment == "..":
            if len(output) > 1:
                output.pop()
        elif segment != ".":
            output.append(segment)
    if segments[-1] in (".", ".."):
        output.append("")
    return "/".join(output) or "/"


def _quote(value: str, safe: str) -> str:
    """Percent-encode characters not allowed in a URL, keeping existing escapes."""
    return quote(_BAD_PERCENT_RE.sub("%25", value), safe=safe)


def _host_key(host: str) -> str:
    """Host used for scope and de-duplication: lowercase, without a leading www."""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _dedup_key(url: str) -> str:
    """Key under which two normalized URLs count as the same resource.

    Ignores the scheme and a leading ``www.`` so ``http://x.gov/a`` and
    ``https://www.x.gov/a`` are fetched and reported only once.
    """
    parts = urlsplit(url)
    return _host_key(parts.netloc) + parts.path + (f"?{parts.query}" if parts.query else "")


def _url_extension(url: str) -> str:
    """Lowercase extension of the last path segment of ``url`` (``""`` if none)."""
    segment = urlsplit(url).path.rsplit("/", 1)[-1]
    return _extension(unquote(segment).split(";", 1)[0])


def _extension(name: str) -> str:
    """Lowercase extension of a file name, without the dot (``""`` if none)."""
    _, dot, ext = name.rpartition(".")
    return ext.strip().lower() if dot else ""


def _short(url: str, limit: int = 150) -> str:
    """Shorten a URL for an error message."""
    return url if len(url) <= limit else url[: limit - 3] + "..."


# --------------------------------------------------------------------------
# Document detection and file names
# --------------------------------------------------------------------------


def _mime(content_type: str | None) -> str | None:
    """Bare lowercase media type from a Content-Type header value."""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def _kind_from_content_type(mime: str | None) -> str | None:
    """Document kind for a bare media type, or ``None``."""
    if not mime:
        return None
    if mime in _EXACT_CONTENT_TYPES:
        return _EXACT_CONTENT_TYPES[mime]
    for prefix, kind in _CONTENT_TYPE_PREFIXES:
        if mime.startswith(prefix):
            return kind
    return None


def _disposition_filename(value: str | None) -> str | None:
    """File name from a Content-Disposition header, preferring ``filename*``."""
    if not value:
        return None
    message = email.message.Message()
    try:
        message["content-disposition"] = value
        params = message.get_params(header="content-disposition") or []
    except Exception:  # malformed header: treat as absent
        return None
    plain: str | None = None
    extended: str | None = None
    for key, param in params[1:]:
        if key.lower() != "filename":
            continue
        if isinstance(param, tuple):  # RFC 2231/5987 filename*=UTF-8''...
            with contextlib.suppress(LookupError, UnicodeError):
                extended = email.utils.collapse_rfc2231_value(param)
        elif param:
            plain = param
    name = (extended or plain or "").strip()
    return name or None


def document_kind(
    url: str, content_type: str | None = None, content_disposition: str | None = None
) -> str | None:
    """Classify a link or response as a document.

    Returns ``"pdf"``, ``"word"``, ``"excel"``, ``"powerpoint"`` or ``"other"``
    for documents and ``None`` for web pages and anything else. A specific
    document Content-Type wins; any other specific type (``text/html``,
    images, ...) means "not a document"; with no type or a generic one
    (``application/octet-stream``) the Content-Disposition file name and then
    the URL extension decide.
    """
    mime = _mime(content_type)
    kind = _kind_from_content_type(mime)
    if kind:
        return kind
    if mime and mime not in _GENERIC_TYPES:
        return None
    name = _disposition_filename(content_disposition)
    if name:
        ext = _extension(name)
        if ext in _SKIPPED_EXTENSIONS:
            return None
        if ext in _EXTENSION_KINDS:
            return _EXTENSION_KINDS[ext]
    return _EXTENSION_KINDS.get(_url_extension(url))


def _url_basename(url: str) -> str:
    """Decoded last path segment of a URL."""
    segment = urlsplit(url).path.rsplit("/", 1)[-1]
    return unquote(segment).split(";", 1)[0]


def _sanitize_filename(name: str) -> str:
    """Make a name safe to show and store: no folders, control or reserved chars."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable())
    name = _UNSAFE_FILENAME_RE.sub("_", name)
    return " ".join(name.split()).strip(" .")


def _ensure_extension(name: str, kind: str) -> str:
    """Add the usual extension for ``kind`` unless the name already has a fitting one."""
    default = _DEFAULT_EXTENSIONS.get(kind)
    if default is None:
        return name
    stem, dot, _ = name.rpartition(".")
    ext = _extension(name)
    if _EXTENSION_KINDS.get(ext) == kind:
        return name
    if dot and stem and ext in _WEB_PAGE_EXTENSIONS:
        name = stem
    return name + default


def _truncate_filename(name: str, limit: int = _MAX_FILENAME) -> str:
    """Shorten a file name to ``limit`` characters, keeping its extension."""
    if len(name) <= limit:
        return name
    stem, ext = os.path.splitext(name)
    if not ext or len(ext) > 16:
        return name[:limit]
    return stem[: limit - len(ext)].rstrip(" .") + ext


def _filename_for(url: str, kind: str, content_disposition: str | None = None) -> str:
    """Display/storage file name for a document."""
    raw = _disposition_filename(content_disposition) or _url_basename(url)
    name = _sanitize_filename(raw) or "document"
    return _truncate_filename(_ensure_extension(name, kind))


# --------------------------------------------------------------------------
# HTML and sitemap parsing
# --------------------------------------------------------------------------


class _LinkExtractor(HTMLParser):
    """Collects raw link targets and the ``<base href>`` from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in reversed(attrs) if value}  # first wins
        target: str | None = None
        if tag == "base":
            if self.base_href is None:
                self.base_href = values.get("href")
        elif tag in ("a", "area"):
            target = values.get("href")
        elif tag in ("iframe", "frame", "embed"):
            target = values.get("src")
        elif tag == "object":
            target = values.get("data")
        elif tag == "link":
            if _kind_from_content_type(_mime(values.get("type"))):
                target = values.get("href")
        elif tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            match = _REFRESH_URL_RE.search(values.get("content", ""))
            target = match.group(1) if match else None
        if target:
            self.links.append(target)


def _extract_links(markup: str, page_url: str) -> list[str]:
    """Normalized, de-duplicated absolute links found in an HTML page."""
    parser = _LinkExtractor()
    with contextlib.suppress(Exception):  # the parser is lenient; never fail a page
        parser.feed(markup)
        parser.close()
    base = normalize_url(parser.base_href, page_url) if parser.base_href else None
    links: dict[str, None] = {}
    for raw in parser.links:
        url = normalize_url(raw, base or page_url)
        if url:
            links[url] = None
    return list(links)


def _decode_page(body: bytes, charset: str | None) -> str:
    """Decode HTML bytes using the declared charset, else UTF-8, else Windows-1252."""
    if charset:
        with contextlib.suppress(LookupError):
            return body.decode(charset, errors="replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def _looks_like_html(head: bytes) -> bool:
    """True when the first bytes of a body look like markup rather than a file."""
    return head[:_SNIFF_BYTES].lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def _parse_sitemap(text: str) -> tuple[bool, list[str]]:
    """Return ``(is_sitemap_index, loc_urls)`` for a sitemap XML document."""
    locs = [html.unescape(loc).strip() for loc in _LOC_RE.findall(text)]
    return bool(_SITEMAP_INDEX_RE.search(text)), [loc for loc in locs if loc]


def _maybe_gunzip(body: bytes, limit: int) -> bytes | None:
    """Decompress a gzip body (at most ``limit`` bytes out); pass others through."""
    if body[:2] != b"\x1f\x8b":
        return body
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, limit)
    except zlib.error:
        return None


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    """Read at most ``limit`` bytes of a streamed response body."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
        chunk = chunk[: limit - size]
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)


def _status_text(status: int) -> str:
    """Plain-language description of an HTTP error status, e.g. "Not found (HTTP 404)"."""
    if status in (404, 410):
        text = "Not found"
    elif status in (401, 403):
        text = "Access denied"
    elif status == 429:
        text = "Too many requests"
    elif status >= 500:
        text = "Website error"
    else:
        text = "HTTP error"
    return f"{text} (HTTP {status})"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _describe_error(exc: BaseException) -> str:
    """Plain-language description of a network or parsing failure."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "the website took too long to respond"
    if isinstance(exc, httpx.TooManyRedirects):
        return "too many redirects"
    if isinstance(exc, httpx.ConnectError):
        return "could not connect to the website"
    if isinstance(exc, httpx.ProxyError):
        return "the network proxy refused the connection"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "the website closed the connection"
    if isinstance(exc, httpx.HTTPError):
        return "network error"
    if isinstance(exc, httpx.InvalidURL):
        return "invalid address"
    return f"unexpected error ({type(exc).__name__})"


async def _call(callback: Callable[..., object] | None, *args: object) -> None:
    """Call a sync or async callback."""
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


class _RateLimiter:
    """Spaces out requests globally by at least ``delay`` seconds."""

    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, delay)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self, stopped: Callable[[], bool]) -> None:
        """Wait for this request's turn; return early once ``stopped()`` is true."""
        if self.delay <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            while (remaining := self._next - loop.time()) > 0:
                if stopped():
                    return
                await asyncio.sleep(min(remaining, 0.25))
            self._next = loop.time() + self.delay


# --------------------------------------------------------------------------
# Crawler
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    url: str
    depth: int
    found_on: str | None


@dataclass(frozen=True)
class _Page:
    url: str
    markup: str


def _normalize_start_url(raw: str) -> str:
    """Validate and normalize what the user typed as the site address."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Enter a website address, for example https://www.exampletown.gov")
    if not _HAS_SCHEME_RE.match(text):
        text = "https:" + text if text.startswith("//") else "https://" + text
    url = normalize_url(text)
    invalid = ValueError(f"{raw.strip()!r} is not a valid website address")
    if url is None:
        raise invalid
    try:
        has_user = urlsplit(text).username is not None
        httpx.URL(url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise invalid from exc
    host = urlsplit(url).hostname or ""
    if has_user or not ("." in host or ":" in host or host == "localhost"):
        raise invalid
    return url


class Crawler:
    """Breadth-first crawler that reports the documents a website links to.

    Pages on the start host (``www.`` ignored), on a host the start URL
    redirects to and, with ``include_subdomains``, on its subdomains are
    crawled. Documents linked from those pages are reported wherever they are
    hosted. Pages are crawled up to ``max_depth`` link hops from the start
    page and documents linked from any crawled page are reported.
    """

    def __init__(
        self,
        start_url: str,
        settings: CrawlSettings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Prepare a crawl of ``start_url`` (``ValueError`` if it is not a website address)."""
        self.settings = settings or CrawlSettings()
        self.start_url = _normalize_start_url(start_url)
        self.stats = CrawlStats()
        self._client = client
        self._headers = {"User-Agent": self.settings.user_agent, "Accept": _ACCEPT}
        self._start_final = self.start_url
        self._scope = {_host_key(urlsplit(self.start_url).hostname or "")}
        self._seen: set[str] = set()  # URLs queued or probed
        self._crawled: set[str] = set()  # pages parsed
        self._documents: set[str] = set()  # documents reported
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        self._robots_blocked: set[str] = set()
        self._sitemap_pages: list[str] = []
        self._sitemap_documents: list[str] = []
        self._pages_reserved = 0
        self._extra_probes = max(0, self.settings.max_pages)
        self._queue_cap = max(1000, _QUEUE_PER_PAGE * self.settings.max_pages)
        self._rate = _RateLimiter(self.settings.delay_seconds)
        self._errors_dropped = 0
        self._stop = False
        self._should_stop: StopCallback | None = None
        self._on_page: PageCallback | None = None
        self._on_document: DocumentCallback | None = None

    async def run(
        self,
        on_page: PageCallback | None = None,
        on_document: DocumentCallback | None = None,
        should_stop: StopCallback | None = None,
    ) -> CrawlStats:
        """Crawl the site and return statistics.

        ``on_page(url, pages_crawled_so_far)`` is called after each HTML page
        is fetched, ``on_document(doc)`` once per unique document, and
        ``should_stop()`` is polled often to cancel. Callbacks may be sync or
        async. Failures of individual URLs are recorded in ``stats.errors``
        and never raised; exceptions raised by the callbacks propagate.
        """
        self._on_page, self._on_document, self._should_stop = on_page, on_document, should_stop
        owns_client = self._client is None
        if owns_client:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.settings.timeout_seconds,
                headers=self._headers,
            )
        try:
            await self._crawl()
        finally:
            if owns_client and self._client is not None:
                await self._client.aclose()
                self._client = None
        return self.stats

    # -- main loop ---------------------------------------------------------

    async def _crawl(self) -> None:
        if self.settings.respect_robots or self.settings.use_sitemaps:
            robots = await self._robots_for(self.start_url)
            delay = robots.crawl_delay(self.settings.user_agent) if robots else None
            if delay and self.settings.respect_robots:
                self._rate.delay = max(self._rate.delay, min(float(delay), _MAX_CRAWL_DELAY))
        self._seen.add(_dedup_key(self.start_url))
        level = [_Item(self.start_url, 0, None)]
        while level and not self._stopped():
            next_level: list[_Item] = []
            await self._run_level(level, next_level)
            if level[0].depth == 0 and self.settings.use_sitemaps and not self._stopped():
                await self._load_sitemaps()
                for url in self._sitemap_pages:
                    self._enqueue(_Item(url, 1, None), next_level)
            level = next_level
        if not self._stopped():
            await self._report_sitemap_documents()
        if self._robots_blocked:
            count = len(self._robots_blocked)
            links = "link" if count == 1 else "links"
            self._add_error(f"Skipped {count} {links} that robots.txt asks crawlers not to visit")
        if self._errors_dropped:
            self.stats.errors[-1] = f"...and {self._errors_dropped + 1} more problems"

    async def _run_level(self, items: list[_Item], next_level: list[_Item]) -> None:
        """Process one depth level with ``concurrency`` workers."""
        pending = iter(items)

        async def worker() -> None:
            for item in pending:
                if self._stopped():
                    return
                await self._process(item, next_level)

        count = max(1, min(self.settings.concurrency, len(items)))
        tasks = [asyncio.create_task(worker()) for _ in range(count)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _process(self, item: _Item, next_level: list[_Item]) -> None:
        """Fetch one queued URL and handle the page or document it turns out to be."""
        can_crawl = (
            item.depth <= self.settings.max_depth and self._pages_reserved < self.settings.max_pages
        )
        if not can_crawl and (self._extra_probes <= 0 or not _LIKELY_DOCUMENT_URL.search(item.url)):
            return
        if not await self._allowed(item.url):
            if item.depth == 0:
                self._add_error(f"robots.txt asks crawlers not to visit {_short(item.url)}")
            else:
                self._robots_blocked.add(_dedup_key(item.url))
            return
        if not can_crawl:
            self._extra_probes -= 1
        await self._rate.wait(self._stopped)
        if self._stopped():
            return
        try:
            async with asyncio.timeout(self.settings.timeout_seconds * 2):
                result = await self._probe(item, can_crawl)
        except Exception as exc:  # one bad URL never stops the crawl
            self._add_error(f"Could not load {_short(item.url)}: {_describe_error(exc)}")
            return
        if isinstance(result, FoundDocument):
            await self._report(result, item.url)
        elif isinstance(result, _Page):
            await self._crawled_page(result, item, next_level)

    def _stream(self, url: str) -> contextlib.AbstractAsyncContextManager[httpx.Response]:
        """Streaming GET with our User-Agent, timeout and redirect following."""
        assert self._client is not None
        return self._client.stream(
            "GET",
            url,
            headers=self._headers,
            follow_redirects=True,
            timeout=self.settings.timeout_seconds,
        )

    async def _probe(self, item: _Item, can_crawl: bool) -> FoundDocument | _Page | None:
        """GET a URL, read its headers and, only for an HTML page, its body."""
        async with self._stream(item.url) as response:
            final = normalize_url(str(response.url)) or item.url
            if item.depth == 0:  # the start URL may redirect, e.g. to www. or a new domain
                self._start_final = final
                self._scope.add(_host_key(urlsplit(final).hostname or ""))
            if response.status_code >= 400:
                self._add_error(f"{_status_text(response.status_code)}: {_short(item.url)}")
                return None
            if not response.is_success:
                return None
            content_type = response.headers.get("content-type")
            disposition = response.headers.get("content-disposition")
            kind = document_kind(final, content_type, disposition)
            if kind:
                # Keep the site's own link when it redirects off-site (e.g. to an
                # expiring storage URL): it is stable and download() follows it.
                url = final if self._in_scope(final) else item.url
                filename = _filename_for(final, kind, disposition)
                return FoundDocument(url, item.found_on, kind, filename, _mime(content_type))
            mime = _mime(content_type)
            if not can_crawl or not self._in_scope(final) or (mime and mime not in _HTML_TYPES):
                return None
            key = _dedup_key(final)
            if key in self._crawled or self._pages_reserved >= self.settings.max_pages:
                return None
            self._crawled.add(key)
            self._seen.add(key)
            self._pages_reserved += 1
            try:
                body = await _read_limited(response, _MAX_PAGE_BYTES)
            except BaseException:
                self._pages_reserved -= 1
                self._crawled.discard(key)
                raise
            if mime is None and not _looks_like_html(body):
                self._pages_reserved -= 1
                return None
            return _Page(final, _decode_page(body, response.charset_encoding))

    async def _crawled_page(self, page: _Page, item: _Item, next_level: list[_Item]) -> None:
        """Count a fetched page, tell the caller, and follow its links."""
        self.stats.pages_crawled += 1
        await _call(self._on_page, page.url, self.stats.pages_crawled)
        for link in _extract_links(page.markup, page.url):
            if self._stopped():
                return
            await self._handle_link(link, item.depth + 1, page.url, next_level)

    async def _handle_link(
        self, url: str, depth: int, found_on: str, next_level: list[_Item]
    ) -> None:
        """Report a document link or queue a page link."""
        if len(url) > _MAX_URL_LENGTH or _url_extension(url) in _SKIPPED_EXTENSIONS:
            return
        kind = document_kind(url)
        if kind is None:
            if self._in_scope(url):
                self._enqueue(_Item(url, depth, found_on), next_level)
            return
        if _dedup_key(url) in self._documents:
            return
        if not await self._allowed(url):
            self._robots_blocked.add(_dedup_key(url))
            return
        await self._report(FoundDocument(url, found_on, kind, _filename_for(url, kind), None))

    def _enqueue(self, item: _Item, next_level: list[_Item]) -> None:
        """Queue a URL for the next level unless seen, useless or over the cap."""
        if item.depth > self.settings.max_depth and not _LIKELY_DOCUMENT_URL.search(item.url):
            return
        key = _dedup_key(item.url)
        if key in self._seen or len(next_level) >= self._queue_cap:
            return
        self._seen.add(key)
        next_level.append(item)

    async def _report(self, document: FoundDocument, *also: str) -> None:
        """Call ``on_document`` the first time a document URL is seen."""
        keys = {_dedup_key(document.url), *(_dedup_key(url) for url in also)}
        is_new = not keys & self._documents
        self._documents |= keys
        if is_new:
            self.stats.documents_found += 1
            await _call(self._on_document, document)

    # -- scope, robots.txt and sitemaps -------------------------------------

    def _in_scope(self, url: str) -> bool:
        """True for URLs on the site being crawled (pages there may be fetched)."""
        host = urlsplit(url).hostname
        if not host:
            return False
        key = _host_key(host)
        if key in self._scope:
            return True
        return self.settings.include_subdomains and any(
            key.endswith("." + base) for base in self._scope
        )

    async def _allowed(self, url: str) -> bool:
        """True unless robots.txt of the (in-scope) host disallows ``url``."""
        if not self.settings.respect_robots or not self._in_scope(url):
            return True
        robots = await self._robots_for(url)
        return robots is None or robots.can_fetch(self.settings.user_agent, url)

    async def _robots_for(self, url: str) -> RobotFileParser | None:
        """Parsed robots.txt for the host of ``url`` (cached; ``None`` = allow all)."""
        parts = urlsplit(url)
        key = _host_key(parts.hostname or "")
        if key not in self._robots:
            async with self._robots_lock:
                if key not in self._robots:
                    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
                    self._robots[key] = await self._fetch_robots(robots_url)
        return self._robots[key]

    async def _fetch_robots(self, robots_url: str) -> RobotFileParser | None:
        try:
            response, body = await self._get_limited(robots_url, _MAX_ROBOTS_BYTES)
        except Exception:
            return None
        if not response.is_success or _looks_like_html(body):
            return None
        parser = RobotFileParser(robots_url)
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        return parser

    async def _get_limited(self, url: str, limit: int) -> tuple[httpx.Response, bytes]:
        """GET a small resource (robots.txt, sitemap), reading at most ``limit`` bytes."""
        await self._rate.wait(self._stopped)
        async with asyncio.timeout(self.settings.timeout_seconds * 2):
            async with self._stream(url) as response:
                body = await _read_limited(response, limit) if response.is_success else b""
        return response, body

    async def _load_sitemaps(self) -> None:
        """Read robots.txt ``Sitemap:`` entries and ``/sitemap.xml`` (one index level)."""
        candidates: list[str] = []
        for site_url in dict.fromkeys((self.start_url, self._start_final)):
            robots = await self._robots_for(site_url)
            candidates.extend((robots.site_maps() or []) if robots else [])
        candidates.append(urljoin(self._start_final, "/sitemap.xml"))
        queue = [(url, 0) for url in dict.fromkeys(filter(None, map(normalize_url, candidates)))]
        seen = {url for url, _ in queue}
        budget = _SITEMAP_URLS_PER_PAGE * max(1, self.settings.max_pages)
        fetched = 0
        while queue and fetched < _MAX_SITEMAP_FILES and budget > 0 and not self._stopped():
            sitemap_url, nesting = queue.pop(0)
            if not await self._allowed(sitemap_url):
                continue
            fetched += 1
            text = await self._fetch_sitemap(sitemap_url)
            if text is None:
                continue
            is_index, locs = _parse_sitemap(text)
            for loc in locs:
                url = normalize_url(loc)
                if url is None:
                    continue
                if is_index:
                    if nesting == 0 and url not in seen:
                        seen.add(url)
                        queue.append((url, 1))
                    continue
                self._add_sitemap_url(url)
                budget -= 1
                if budget <= 0:
                    break

    async def _fetch_sitemap(self, url: str) -> str | None:
        """Sitemap XML text, or ``None`` if missing or unreadable (that's normal)."""
        try:
            response, body = await self._get_limited(url, _MAX_SITEMAP_BYTES)
        except Exception:
            return None
        if not response.is_success or not body:
            return None
        data = _maybe_gunzip(body, _MAX_SITEMAP_BYTES)
        return data.decode("utf-8", errors="replace") if data else None

    def _add_sitemap_url(self, url: str) -> None:
        if len(url) > _MAX_URL_LENGTH or _url_extension(url) in _SKIPPED_EXTENSIONS:
            return
        if document_kind(url):
            self._sitemap_documents.append(url)
        elif self._in_scope(url):
            self._sitemap_pages.append(url)

    async def _report_sitemap_documents(self) -> None:
        """Report sitemap documents no crawled page linked to (``found_on`` None)."""
        for url in self._sitemap_documents:
            if self._stopped():
                return
            if _dedup_key(url) in self._documents:
                continue
            if not await self._allowed(url):
                self._robots_blocked.add(_dedup_key(url))
                continue
            kind = document_kind(url) or "other"
            await self._report(FoundDocument(url, None, kind, _filename_for(url, kind), None))

    # -- bookkeeping ----------------------------------------------------------

    def _stopped(self) -> bool:
        if not self._stop and self._should_stop is not None and self._should_stop():
            self._stop = True
        return self._stop

    def _add_error(self, message: str) -> None:
        if len(self.stats.errors) < _MAX_ERRORS:
            self.stats.errors.append(message)
        else:
            self._errors_dropped += 1


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):g} MB"
    if size >= 1024:
        return f"{size / 1024:g} KB"
    return f"{size} bytes"


def _sniff(head: bytes) -> str | None:
    """``"pdf"``, ``"office"`` (OOXML/ODF zip or legacy OLE) or ``"rtf"`` from magic bytes."""
    if b"%PDF-" in head[:_SNIFF_BYTES]:
        return "pdf"
    if head.startswith((b"PK", b"\xd0\xcf\x11\xe0")):
        return "office"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return None


def _check_body(head: bytes, expected: str | None, mime: str | None) -> None:
    """Raise :class:`DownloadError` unless the body is the kind of document claimed."""
    sniffed = _sniff(head)
    if sniffed is None:
        if not head:
            raise DownloadError("The file is empty")
        if mime in _HTML_TYPES or _looks_like_html(head):
            raise DownloadError("The link returned a web page, not a document")
        raise DownloadError("The file is not a PDF or Office document")
    if expected == "pdf" and sniffed != "pdf":
        raise DownloadError(
            "The link should be a PDF, but the file is a different kind of document"
        )
    if expected in ("word", "excel", "powerpoint") and sniffed == "pdf":
        label = _KIND_LABELS[expected]
        raise DownloadError(f"The link should be a {label} file, but the file is a PDF")


async def _stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    handle: BinaryIO,
    max_bytes: int,
    headers: dict[str, str] | None,
) -> tuple[str | None, int, str]:
    """Stream ``url`` into an open binary file, validating size and magic bytes."""
    too_large = DownloadError(f"The file is larger than the {_format_size(max_bytes)} limit")
    async with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
        if response.status_code >= 400:
            raise DownloadError(
                f"Could not download the file: {_lower_first(_status_text(response.status_code))}"
            )
        if not response.is_success:
            raise DownloadError(f"Could not download the file (HTTP {response.status_code})")
        content_type = response.headers.get("content-type")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise too_large
        expected = document_kind(
            str(response.url), content_type, response.headers.get("content-disposition")
        )
        digest = hashlib.sha256()
        size = 0
        head = b""
        checked = False
        async for chunk in response.aiter_bytes(_CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise too_large
            if not checked:
                head += chunk[: _SNIFF_BYTES - len(head)]
                if len(head) >= _SNIFF_BYTES:
                    _check_body(head, expected, _mime(content_type))
                    checked = True
            digest.update(chunk)
            handle.write(chunk)
        if not checked:
            _check_body(head, expected, _mime(content_type))
    return _mime(content_type), size, digest.hexdigest()


async def download(
    client: httpx.AsyncClient,
    url: str,
    dest_path: str,
    *,
    max_bytes: int = 100 * 1024 * 1024,
    user_agent: str | None = None,
) -> tuple[str | None, int, str]:
    """Stream ``url`` to ``dest_path``; return ``(content_type, size_bytes, sha256_hex)``.

    ``content_type`` is the bare media type (e.g. ``"application/pdf"``).
    Redirects are followed. The file is written to a temporary file in the
    destination folder and renamed into place only when complete, so
    ``dest_path`` never holds a partial file. Raises :class:`DownloadError`
    with a plain-language message on HTTP and network errors, files over
    ``max_bytes``, and bodies that are not the document they claim to be
    (e.g. an HTML error page served for a PDF link).
    """
    headers = {"User-Agent": user_agent} if user_agent else None
    folder = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=folder, prefix=".download-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            try:
                result = await _stream_to_file(client, url, handle, max_bytes, headers)
            except httpx.TimeoutException as exc:
                raise DownloadError("The website took too long to respond") from exc
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                raise DownloadError(f"Could not download the file: {_describe_error(exc)}") from exc
        os.replace(temp_path, dest_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise
    return result
