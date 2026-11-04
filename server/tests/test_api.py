# SPDX-License-Identifier: AGPL-3.0-or-later
import http.server
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import samples  # from core/tests
from readpath_server.api import create_app
from readpath_server.config import Settings


def make_settings(tmp_path: Path, **over) -> Settings:
    values = dict(data_dir=tmp_path / "data", web_dir=tmp_path / "web", workers=2, default_lang="en-US",
                  max_upload_mb=20, max_pages_limit=50, verapdf_on_scan=False, allow_private_hosts=True,
                  password=None, max_check_pages=None)
    values.update(over)
    return Settings(**values)


@pytest.fixture()
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def council_bytes(tmp_path):
    return samples.council_minutes(tmp_path / "council.pdf").read_bytes()


def wait_scan(client, scan_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        scan = client.get(f"/api/scans/{scan_id}").json()
        if scan["status"] in ("done", "failed", "cancelled"):
            return scan
        time.sleep(0.2)
    raise AssertionError(f"scan did not finish: {scan}")


def upload(client, data: bytes, name="council.pdf"):
    r = client.post("/api/uploads", files=[("files", (name, data, "application/pdf"))])
    assert r.status_code == 200, r.text
    scan = wait_scan(client, r.json()["id"])
    docs = client.get(f"/api/scans/{scan['id']}/documents").json()
    return scan, docs


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and set(h) >= {"verapdf", "vision", "ocr", "version"}


def test_upload_check_fix_review(client, council_bytes):
    scan, docs = upload(client, council_bytes)
    assert scan["kind"] == "upload" and scan["summary"]["pdfs"] == 1
    doc = docs[0]
    assert doc["status"] == "checked" and doc["grade"] == "auto_fixable"
    assert scan["summary"]["auto_fixable"] == 1

    report = client.get(f"/api/documents/{doc['id']}").json()
    assert report["checks"][0]["status"] == "fail"
    before = report["document"]["score"]

    report = client.post(f"/api/documents/{doc['id']}/autofix").json()
    assert report["document"]["score"] > before
    assert report["document"]["fixed"] and report["document"]["revision"] == 1
    assert any("Added tags" in f for f in report["fixes_applied"])

    did = doc["id"]
    ov = client.get(f"/api/documents/{did}/review").json()
    assert ov["tagged"] and ov["page_count"] == 3 and ov["revision"] == 1
    rev = ov["revision"]

    r = client.put(f"/api/documents/{did}/metadata", json={"revision": rev, "title": "Council Minutes", "lang": "en-US"})
    assert r.status_code == 200 and r.json()["title"] == "Council Minutes"
    rev = r.json()["revision"]
    rev = client.post(f"/api/documents/{did}/review/basics/complete", json={"revision": rev}).json()["revision"]

    # stale revision is rejected
    stale = client.put(f"/api/documents/{did}/metadata", json={"revision": 0, "title": "X y z"})
    assert stale.status_code == 409

    blocks = client.get(f"/api/documents/{did}/pages/1/blocks").json()
    assert blocks["blocks"][0]["type"] == "H1"
    ids = [b["id"] for b in blocks["blocks"]]
    r = client.put(f"/api/documents/{did}/pages/1/order", json={"revision": rev, "block_ids": ids[1:] + ids[:1]})
    assert r.status_code == 200
    rev = r.json()["revision"]
    assert r.json()["blocks"][-1]["type"] == "H1"
    first = r.json()["blocks"][0]
    r = client.put(f"/api/documents/{did}/pages/1/blocks/{first['id']}", json={"revision": rev, "type": "H1"})
    assert r.status_code == 200 and r.json()["blocks"][0]["type"] == "H1"
    rev = r.json()["revision"]
    bad = client.put(f"/api/documents/{did}/pages/1/blocks/{first['id']}", json={"revision": rev, "type": "Nope"})
    assert bad.status_code == 422

    figs = client.get(f"/api/documents/{did}/figures").json()
    fig = figs["figures"][0]
    img = client.get(fig["image_url"])
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    r = client.put(f"/api/documents/{did}/figures/{fig['id']}", json={"revision": rev, "alt": "Bar chart of spending."})
    assert r.json()["figures"][0]["alt"] == "Bar chart of spending."
    rev = r.json()["revision"]
    assert client.post(f"/api/documents/{did}/figures/{fig['id']}/suggest").status_code == 503

    tables = client.get(f"/api/documents/{did}/tables").json()["tables"]
    assert tables[0]["header_row"] and len(tables[0]["rows"]) == 4
    assert client.get(tables[0]["image_url"]).status_code == 200
    r = client.put(f"/api/documents/{did}/tables/{tables[0]['id']}",
                   json={"revision": rev, "header_row": True, "header_column": True})
    assert r.json()["tables"][0]["header_column"]
    rev = r.json()["revision"]
    for step in ("reading-order", "images", "tables"):
        rev = client.post(f"/api/documents/{did}/review/{step}/complete", json={"revision": rev}).json()["revision"]
    ov = client.get(f"/api/documents/{did}/review").json()
    assert all(s["status"] in ("done", "not_needed") for s in ov["steps"]), ov["steps"]

    final = client.post(f"/api/documents/{did}/finish", json={"revision": rev}).json()
    assert final["document"]["grade"] == "ready"
    assert final["document"]["score"] >= 95

    page = client.get(f"/api/documents/{did}/pages/1/image?width=400")
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"
    etag = page.headers["etag"]
    assert client.get(f"/api/documents/{did}/pages/1/image?width=400", headers={"If-None-Match": etag}).status_code == 304

    dl = client.get(f"/api/documents/{did}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    assert "council-accessible.pdf" in dl.headers["content-disposition"]
    orig = client.get(f"/api/documents/{did}/download?version=original")
    assert orig.content == council_bytes

    reverted = client.post(f"/api/documents/{did}/revert").json()
    assert not reverted["document"]["fixed"] and reverted["document"]["score"] == before

    csv_text = client.get(f"/api/scans/{scan['id']}/report.csv").text
    assert "council.pdf" in csv_text.splitlines()[1]


def test_scan_autofix_and_delete(client, council_bytes):
    scan, docs = upload(client, council_bytes)
    r = client.post(f"/api/scans/{scan['id']}/autofix")
    assert r.status_code == 200
    scan = wait_scan(client, scan["id"])
    docs = client.get(f"/api/scans/{scan['id']}/documents").json()
    assert docs[0]["fixed"] and docs[0]["grade"] == "needs_help"
    assert client.delete(f"/api/scans/{scan['id']}").status_code == 204
    assert client.get(f"/api/scans/{scan['id']}").status_code == 404


def test_upload_rejects_non_pdf(client):
    r = client.post("/api/uploads", files=[("files", ("x.pdf", b"<html></html>", "application/pdf"))])
    assert r.status_code == 422


def test_bad_scan_url(client):
    assert client.post("/api/scans", json={"url": "not a url"}).status_code == 422


def test_private_hosts_blocked(tmp_path):
    app = create_app(make_settings(tmp_path, allow_private_hosts=False))
    with TestClient(app) as c:
        r = c.post("/api/scans", json={"url": "http://127.0.0.1:9/"})
        assert r.status_code == 422 and "private" in r.json()["detail"]


def test_password(tmp_path):
    app = create_app(make_settings(tmp_path, password="s3cret"))
    with TestClient(app) as c:
        assert c.get("/api/scans").status_code == 401
        assert c.get("/api/scans", auth=("clerk", "s3cret")).status_code == 200
        assert c.get("/api/health").status_code == 200


def test_spa_fallback(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Readpath</title>")
    (web / "app.js").write_text("console.log(1)")
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        assert "Readpath" in c.get("/scans/abc").text
        assert c.get("/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404
        assert c.get("/../../etc/passwd").status_code in (200, 404)
        assert "root:" not in c.get("/..%2F..%2Fetc%2Fpasswd").text


@pytest.fixture()
def site(tmp_path, council_bytes):
    root = tmp_path / "site"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "minutes.pdf").write_bytes(council_bytes)
    (root / "index.html").write_text(
        '<html><body><a href="/about.html">About</a> <a href="/docs/minutes.pdf">Minutes</a>'
        ' <a href="/budget.docx">Budget</a></body></html>')
    (root / "about.html").write_text('<html><body><a href="/docs/minutes.pdf#page=2">again</a></body></html>')
    (root / "budget.docx").write_bytes(b"PK\x03\x04fake")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_crawl_end_to_end(client, site):
    r = client.post("/api/scans", json={"url": site, "max_pages": 10})
    assert r.status_code == 200, r.text
    scan = wait_scan(client, r.json()["id"])
    assert scan["status"] == "done", scan
    assert scan["pages_crawled"] >= 2
    docs = client.get(f"/api/scans/{scan['id']}/documents").json()
    kinds = sorted(d["kind"] for d in docs)
    assert kinds == ["pdf", "word"]
    pdf = next(d for d in docs if d["kind"] == "pdf")
    assert pdf["status"] == "checked" and pdf["found_on"].startswith(site) and pdf["score"] is not None
    word = next(d for d in docs if d["kind"] == "word")
    assert word["status"] == "not_checked"
    assert scan["summary"]["other_documents"] == 1
