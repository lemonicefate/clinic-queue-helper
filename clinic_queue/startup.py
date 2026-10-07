"""Runtime startup helpers for the Windows launcher."""

from __future__ import annotations

import errno
import logging
import sqlite3
import socket
import sys

import uvicorn

from .app import create_app
from .config import AppConfig


class StartupError(RuntimeError):
    """Raised when the service cannot bind its configured listener."""


def initialize_runtime(config: AppConfig) -> logging.Logger:
    """Prepare application-owned storage and local logging before serving requests."""
    logger = logging.getLogger("clinic_queue")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(config.sqlite_path):
            pass
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        file_handler = logging.FileHandler(config.log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(file_handler)
        logger.info("Clinic Queue Helper starting")
    except (OSError, sqlite3.Error) as exc:
        raise StartupError(
            f"Cannot initialize application storage outside the HIS directory: {exc}"
        ) from exc
    return logger


def ensure_port_available(host: str, port: int) -> None:
    """Raise an actionable error when another process owns the configured port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((host, port))
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            raise StartupError(
                f"Port {port} on {host} is already in use. Stop the other application "
                "or change the configured port."
            ) from exc
        raise StartupError(f"Cannot bind port {port} on {host}: {exc}") from exc


def start_server(config: AppConfig) -> int:
    try:
        ensure_port_available(config.bind_host, config.port)
        logger = initialize_runtime(config)
    except StartupError as exc:
        print(f"Clinic Queue Helper startup error: {exc}", file=sys.stderr)
        return 2

    logger.info("Serving on %s:%s", config.bind_host, config.port)
    try:
        application = create_app(config)
    except sqlite3.Error as exc:
        logger.exception("Cannot open the local SQLite queue database")
        print(
            f"Clinic Queue Helper startup error: cannot open SQLite queue database "
            f"{config.sqlite_path}: {exc}. Check the configured local storage path.",
            file=sys.stderr,
        )
        return 2
    uvicorn.run(application, host=config.bind_host, port=config.port)
    return 0
