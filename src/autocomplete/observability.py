from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LOGGERS: dict[tuple[str, str, int, int, int], logging.Logger] = {}
_CONFIG: LogConfig | None = None
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 5


def _boolean(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _positive_integer(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class LogConfig:
    directory: Path
    level: int
    max_bytes: int
    backup_count: int
    query_text: bool
    detailed_profiling: bool

    def enables(self, level: int = logging.INFO) -> bool:
        return level >= self.level


def get_config() -> LogConfig:
    """Return the process configuration, parsed once until reset_for_tests()."""

    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    with _LOCK:
        if _CONFIG is None:
            level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
            level = (
                logging.CRITICAL + 1
                if level_name == "OFF"
                else getattr(logging, level_name, logging.INFO)
            )
            _CONFIG = LogConfig(
                Path(os.environ.get("LOG_DIRECTORY", "logs")),
                level,
                _positive_integer("LOG_MAX_BYTES", _DEFAULT_MAX_BYTES),
                _positive_integer("LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT),
                _boolean("LOG_QUERY_TEXT"),
                _boolean("DETAILED_PROFILING"),
            )
    return _CONFIG


def is_enabled(level: int = logging.INFO) -> bool:
    return get_config().enables(level)


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
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        except (OSError, ValueError):
            logger.addHandler(logging.NullHandler())
        _LOGGERS[key] = logger
        return logger


def short_id() -> str:
    return uuid.uuid4().hex


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


def safe_reason(error: BaseException) -> str:
    """Return a bounded reason code without exception text or filesystem paths."""

    return type(error).__name__


def event(kind: str, name: str, level: int = logging.INFO, **fields: Any) -> None:
    """Write one unambiguous JSON event; all logging failures are non-fatal."""

    try:
        config = get_config()
        if not config.enables(level):
            return
        logger = _logger(kind, config)
        payload = {
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": logging.getLevelName(level),
            "event": name,
            **fields,
        }
        logger.log(
            level,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        return


def reset_for_tests() -> None:
    global _CONFIG
    with _LOCK:
        for logger in _LOGGERS.values():
            handlers = list(logger.handlers)
            logger.handlers.clear()
            for handler in handlers:
                handler.close()
        _LOGGERS.clear()
        _CONFIG = None
