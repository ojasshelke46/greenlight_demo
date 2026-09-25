import json
import logging
import logging.config
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

REDACTED = "[REDACTED]"
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s\"',]+")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message", "run_id"}

_secrets: tuple[str, ...] = ()

LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "app.log.JsonFormatter"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        # Empty handler lists drop uvicorn's own text handlers so everything goes through root.
        "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
        "uvicorn.error": {"handlers": [], "propagate": True},
        # Off: RequestContextMiddleware logs each request with its run id, and without query strings.
        "uvicorn.access": {"handlers": [], "propagate": False},
        "httpx": {"level": "WARNING"},
    },
}


def register_secrets(*values: str | None) -> None:
    variants = set()
    for value in values:
        if value and len(value) >= 4:
            variants.add(value)
            variants.add(json.dumps(value)[1:-1])
    global _secrets
    _secrets = tuple(sorted(variants, key=len, reverse=True))


def redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, REDACTED)
    text = _BEARER.sub(rf"\g<1>{REDACTED}", text)
    return _GITHUB_TOKEN.sub(REDACTED, text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "run_id": getattr(record, "run_id", None),
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, default=str))


_base_record_factory = logging.getLogRecordFactory()


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    # Stamped at creation, so the run id is right even if the record is formatted later.
    record = _base_record_factory(*args, **kwargs)
    record.run_id = run_id_var.get()
    return record


def configure_logging(secrets: tuple[str | None, ...] = ()) -> None:
    logging.config.dictConfig(LOGGING_CONFIG)
    logging.setLogRecordFactory(_record_factory)
    register_secrets(*secrets)
