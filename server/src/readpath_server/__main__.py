# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the server: ``readpath-server`` or ``python -m readpath_server``."""

from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="readpath-server", description="Run the Readpath web app.")
    parser.add_argument("--host", default=os.environ.get("READPATH_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("READPATH_PORT", "8000")))
    parser.add_argument("--data", help="data directory (default ./data or $READPATH_DATA)")
    args = parser.parse_args()
    if args.data:
        os.environ["READPATH_DATA"] = args.data
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
