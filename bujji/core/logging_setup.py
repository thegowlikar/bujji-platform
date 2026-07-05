"""Structured logging setup.

Logs are emitted both as human-readable lines to the console and as
line-delimited JSON to a daily rotating file, so every evaluation cycle and
state transition is machine-parseable by the dashboard.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach any structured extras passed via ``extra={"data": {...}}``.
        if hasattr(record, "data") and isinstance(record.data, dict):
            payload.update(record.data)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure the root ``bujji`` logger and return it."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bujji")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(console)

    logfile = Path(log_dir) / f"bujji_{date.today().isoformat()}.jsonl"
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    return logger


def log_event(logger: logging.Logger, msg: str, **data: Any) -> None:
    """Helper to emit a structured event carrying key/value context."""
    logger.info(msg, extra={"data": data})
