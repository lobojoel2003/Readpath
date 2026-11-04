# readpath-server

The Readpath web app's server: website crawler, background check/fix jobs,
SQLite storage, the HTTP API described in [docs/api.md](../docs/api.md), and
hosting for the built web app. Licensed **AGPL-3.0-or-later**.

The PDF engine lives in [readpath-core](../core) (Apache-2.0).

```bash
pip install ../core . && readpath-server --port 8000
```

See the [main README](../README.md) for settings and Docker instructions.
