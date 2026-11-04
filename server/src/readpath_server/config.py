# SPDX-License-Identifier: AGPL-3.0-or-later
"""Server settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _default_web_dir() -> Path:
    if (Path.cwd() / "web" / "dist").is_dir():  # running from a source checkout
        return Path.cwd() / "web" / "dist"
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "web" / "dist"
        if candidate.is_dir():
            return candidate
    return Path("/app/web/dist")


@dataclass
class Settings:
    data_dir: Path
    web_dir: Path
    workers: int
    default_lang: str
    max_upload_mb: int
    max_pages_limit: int
    verapdf_on_scan: bool
    allow_private_hosts: bool
    password: str | None
    max_check_pages: int | None

    @classmethod
    def from_env(cls) -> "Settings":
        data = Path(os.environ.get("READPATH_DATA", "./data")).resolve()
        web = Path(os.environ["READPATH_WEB_DIR"]).resolve() if os.environ.get("READPATH_WEB_DIR") else _default_web_dir()
        max_check = _int("READPATH_MAX_CHECK_PAGES", 0)
        return cls(
            data_dir=data,
            web_dir=web,
            workers=max(1, _int("READPATH_WORKERS", 2)),
            default_lang=os.environ.get("READPATH_DEFAULT_LANG", "en-US"),
            max_upload_mb=max(1, _int("READPATH_MAX_UPLOAD_MB", 100)),
            max_pages_limit=max(1, _int("READPATH_MAX_PAGES_LIMIT", 5000)),
            verapdf_on_scan=_bool("READPATH_VERAPDF_ON_SCAN", False),
            allow_private_hosts=_bool("READPATH_ALLOW_PRIVATE_HOSTS", False),
            password=os.environ.get("READPATH_PASSWORD") or None,
            max_check_pages=max_check or None,
        )
