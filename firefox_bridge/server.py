"""Uvicorn entry point for the Firefox bridge."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from .app import create_app
from .config import get_settings
from .logging_config import configure_logging, get_logger

app = create_app()
logger = get_logger()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local Firefox bridge")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "token"),
        default="run",
        help="run the server (default) or print the configured token",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "token":
        print(settings.token)
        return
    configure_logging()
    logger.info(
        "Firefox bridge starting on %s",
        settings.url,
        extra={"transport": "http+websocket", "method": "server.start"},
    )
    uvicorn.run(app, host=settings.bind_host, port=settings.port)


if __name__ == "__main__":
    main()
