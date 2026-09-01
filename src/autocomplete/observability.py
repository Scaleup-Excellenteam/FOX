from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LOGGERS: dict[tuple[str, str, int, int, int], logging.Logger] = {}
_SAFE = re.compile(r"^[A-Za-z0-9._:/,+-]*$")


def _boolean(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LogConfig:
    directory: Path
    level: int
    max_bytes: int
    backup_count: int
    query_text: bool
    detailed_profiling: bool


def get_config() -> LogConfig:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = (
        logging.CRITICAL + 1
        if level_name == "OFF"
        else getattr(logging, level_name, logging.INFO)
    )
    return LogConfig(
        Path(os.environ.get("LOG_DIRECTORY", "logs")),
        level,
        _integer("LOG_MAX_BYTES", 10 * 1024 * 1024, 1),
        _integer("LOG_BACKUP_COUNT", 5, 0),
        _boolean("LOG_QUERY_TEXT"),
        _boolean("DETAILED_PROFILING"),
    )


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime


class _SafeRotatingHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        return


def _logger(kind: str, config: LogConfig) -> logging.Logger:
    key = (
        kind,
        str(config.directory.absolute()),
        config.max_bytes,
        config.backup_count,
        config.level,
    )
    with _LOCK:
        if key in _LOGGERS:
            return _LOGGERS[key]
        logger = logging.getLogger(
            "autocomplete.observability." + ".".join(map(str, key))
        )
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(config.level)
        try:
            config.directory.mkdir(parents=True, exist_ok=True)
            handler = _SafeRotatingHandler(
                config.directory / f"{kind}.log",
                maxBytes=config.max_bytes,
                backupCount=config.backup_count,
                encoding="utf-8",
            )
            handler.setFormatter(
                _UtcFormatter(
                    fmt="%(asctime)s.%(msecs)03dZ | %(levelname)s | %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
            logger.addHandler(handler)
        except (OSError, ValueError):
            logger.addHandler(logging.NullHandler())
        _LOGGERS[key] = logger
        return logger


def short_id() -> str:
    return uuid.uuid4().hex[:8]


def human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.1f}_{unit}"
        amount /= 1024
    raise AssertionError


def safe_name(path: Path) -> str:
    return Path(path).name or "."


def safe_reason(error: BaseException | str) -> str:
    return str(error).replace("\n", " ").replace("\r", " ")[:240]


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.3f}"
    raw = str(value).replace("\n", " ").replace("\r", " ")
    if _SAFE.fullmatch(raw):
        return raw
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def event(kind: str, name: str, level: int = logging.INFO, **fields: Any) -> None:
    try:
        config = get_config()
        logger = _logger(kind, config)
        if logger.isEnabledFor(level):
            logger.log(
                level,
                " | ".join(
                    [name, *(f"{key}={_value(value)}" for key, value in fields.items())]
                ),
            )
    except Exception:
        return


def reset_for_tests() -> None:
    with _LOCK:
        for logger in _LOGGERS.values():
            for handler in logger.handlers:
                handler.close()
        _LOGGERS.clear()
