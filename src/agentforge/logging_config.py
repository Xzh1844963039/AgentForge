"""Logging configuration for AgentForge."""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "agentforge"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    *,
    force: bool = True,
) -> logging.Logger:
    """Configure the package logger with console and optional file handlers."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if force and logger.handlers:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the package namespace."""
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)
