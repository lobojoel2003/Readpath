# Contributing to Readpath

Thanks for helping make public documents readable for everyone. Bug reports,
sample PDFs that Readpath gets wrong, fixes and plain-language improvements are
all welcome.

## Ground rules

- **Never make a fix that changes how a page looks.** The tagging tests render
  every page before and after and compare pixels. Font substitution is the one
  exception and is limited to exact-width fonts.
- **Guesses must be confirmable.** If Readpath infers something (a title, the
  reading order, table headers) the checker should ask a person to confirm it.
- **Write for a city clerk.** Messages in checks and the UI use short sentences
  and avoid jargon. WCAG and PDF/UA references go in small print.
- **The app must stay WCAG 2.1 AA** itself: keyboard operable, labelled
  controls, visible focus, 4.5:1 contrast, no drag-only interactions.

## Repository layout

| Path | What | License |
|---|---|---|
| `core/` | `readpath-core`: the PDF engine (checks, tagging, fixes, review API, CLI) | Apache-2.0 |
| `server/` | `readpath-server`: crawler, jobs, SQLite, HTTP API | AGPL-3.0-or-later |
| `web/` | React + TypeScript web app | AGPL-3.0-or-later |
| `docker/`, `Dockerfile`, `docker-compose*.yml` | Packaging | AGPL-3.0-or-later |
| `scripts/` | Release helpers and the container smoke test | AGPL-3.0-or-later |

Contributions to `core/` are licensed under Apache-2.0 and everything else
under AGPL-3.0-or-later, matching the files you change.

## Development setup

You need Python 3.11+, Node 20.19+ and, optionally, Java 21 for veraPDF.

```bash
python -m venv .venv && . .venv/bin/activate
```

```bash
pip install -e "./core[test]" -e "./server[test]" ruff
```

```bash
cd web && npm ci && npm run dev
```

Run the API in another terminal (the web dev server proxies `/api` to it):

```bash
readpath-server --port 8000
```

## Tests

```bash
cd core && pytest
```

```bash
cd server && pytest
```

```bash
ruff check .
```

```bash
cd web && npm run build
```

The veraPDF conformance tests in `core/tests/test_conformance.py` run when
veraPDF is installed. To set it up locally the same way CI does:

```bash
mvn -q -f docker/verapdf/pom.xml org.apache.maven.plugins:maven-dependency-plugin:3.8.1:copy-dependencies -DoutputDirectory="$HOME/.verapdf/lib"
```

Then point `READPATH_VERAPDF` at a script that runs
`java -cp "$HOME/.verapdf/lib/*" org.verapdf.apps.GreenfieldCliWrapper "$@"`.

To test the Docker image end to end:

```bash
docker build -t readpath:dev . && docker run -d -p 8000:8000 readpath:dev
```

```bash
python core/tests/samples.py /tmp/samples && python scripts/smoke_test.py http://localhost:8000 /tmp/samples --expect-ocr --expect-verapdf --expect-fonts
```

## Reporting a PDF that Readpath gets wrong

Open an issue with the PDF attached (only if it is public), what Readpath did,
and what you expected. If the file can't be shared, the output of
`readpath check --json file.pdf` helps.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `build:`, `refactor:`, `style:`, `chore:`.
- Add a test for every bug fix, and a line under **Unreleased** in
  [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.
- CI must pass: lint, core tests on Python 3.11–3.13, server tests, veraPDF
  conformance, the web build and the Docker smoke test.

## Releasing

1. Update the version in `core/pyproject.toml`, `server/pyproject.toml`,
   `web/package.json` and both `__init__.py` files. Check them with
   `python scripts/check_version.py`.
2. Move the **Unreleased** notes in `CHANGELOG.md` under the new version.
3. Tag and push:

```bash
git tag -a v0.2.0 -m "Readpath 0.2.0" && git push origin v0.2.0
```

   Or, on GitHub, open **Releases → Draft a new release**, type the new tag
   (`v0.2.0`), pick the commit to release, and publish. Creating the tag starts
   the same workflow, which then fills in the release notes and files.

The release workflow reruns CI, publishes the Docker image to
`ghcr.io/lobojoel2003/readpath`, builds the Python packages and web bundle,
and creates the GitHub Release with the changelog notes.
