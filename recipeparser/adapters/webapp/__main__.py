"""Start the web app:  python -m recipeparser.adapters.webapp [--port 8765] [--data-dir DIR]"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    from recipeparser.adapters.webapp.instrument import ConsoleFormatter
    from recipeparser.adapters.webapp.server import create_app, default_data_dir
    from recipeparser.paths import get_env_file

    parser = argparse.ArgumentParser(
        prog="python -m recipeparser.adapters.webapp",
        description="RecipeParser web app: run, watch and control extractions.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to listen on (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RP_WEBAPP_PORT", "8765")))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Where runs, the database and uploads live (default: app data dir /webapp, or RP_WEBAPP_DATA_DIR).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="The .env file secrets are read from and saved to (default: the app's .env, shared "
        "with the CLI and desktop GUI).",
    )
    args = parser.parse_args(argv)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(level_name)
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ConsoleFormatter("server"))
    root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else logging.INFO)

    data_dir = args.data_dir or default_data_dir()
    env_file = args.env_file or get_env_file()
    app = create_app(data_dir, env_file)

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    logging.getLogger("recipeparser.adapters.webapp").info("Open %s in your browser.", url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
