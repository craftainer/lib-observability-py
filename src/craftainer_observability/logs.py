"""Structured stdout logging, optionally bridged to an OTEL collector.

Always logs structured JSON to stdout; additionally exports logs via OTLP when
OTEL_EXPORTER_OTLP_ENDPOINT is set. Traces and metrics live in `otel.py`,
which shares this module's `shared_resource()`. OTEL's own env vars (endpoint, headers,
protocol, compression, certificate) are read directly by OTLPLogExporter() itself,
not re-modeled as application settings -- OTEL's env-var convention is already the
single source of truth for those, vendor-neutral. That includes the exported
`service.name`, which `Resource.create()` takes from OTEL_SERVICE_NAME on its own.
When a span is active, every JSON line also carries `trace_id`/`span_id`; inside a request
with `RequestIDMiddleware` it also carries `request_id`.
This module imports nothing from `app`.
"""

import copy
import json
import logging
import os
import sys
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from craftainer_observability.middleware import get_request_id
from craftainer_observability.redaction import redact_text, redact_value

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def otlp_enabled() -> bool:
    """Whether OTLP telemetry is on: an endpoint is set and `OTEL_SDK_DISABLED` isn't true."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() != "true"


def exporter_enabled(signal: str) -> bool:
    """Whether `OTEL_<SIGNAL>_EXPORTER` (`traces`/`metrics`/`logs`) allows OTLP export.

    Unset means `otlp` (the OTel default); `none` switches that one signal off.
    """
    value = os.environ.get(f"OTEL_{signal.upper()}_EXPORTER", "otlp")
    names = {name.strip().lower() for name in value.split(",")}
    return "none" not in names


@lru_cache(maxsize=1)
def shared_resource() -> Resource:
    """The one `Resource` all three signals share.

    `service.name` comes from `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` (read by
    `Resource.create()` itself). `service.version` comes from `APP_VERSION`, unless
    `OTEL_RESOURCE_ATTRIBUTES` already sets it (the environment variable wins).
    """
    resource = Resource.create()
    version = os.environ.get("APP_VERSION")
    if version and "service.version" not in resource.attributes:
        resource = resource.merge(Resource({"service.version": version}))
    return resource


class RedactingFilter(logging.Filter):
    """Record-level redaction, installed on every handler `configure_logging` adds.

    Returns a redacted copy (Python 3.12+ handlers use a filter's returned record), so the
    stdout and the OTLP handler each redact on their own and a host's handlers still see
    the original. It covers the message (`msg` with `args` applied), every `extra=` field
    (by key, with the shared `redaction.REDACTION_KEYS`, and by value), `stack_info` and
    the exception: the traceback becomes redacted `exc_text`, and, with
    `otel_exception_attributes`, the OTel `exception.*` attributes the bridge would
    otherwise build from the raw `exc_info`.
    """

    def __init__(self, *, otel_exception_attributes: bool = False) -> None:
        """Build the filter; the OTLP handler's passes `otel_exception_attributes=True`."""
        super().__init__()
        self._otel = otel_exception_attributes

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        """Return a redacted copy of `record`."""
        clean = copy.copy(record)
        clean.msg = redact_text(record.getMessage())
        clean.args = None
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                clean.__dict__[key] = redact_value(key, value)
        if record.stack_info:
            clean.stack_info = redact_text(record.stack_info)
        if record.exc_info is not None and record.exc_info[0] is not None:
            clean.exc_text = redact_text(_format_exception(record.exc_info))
            clean.exc_info = None
            if self._otel:
                exc_type, exc_value, _ = record.exc_info
                clean.__dict__["exception.type"] = exc_type.__qualname__
                clean.__dict__["exception.message"] = redact_text(str(exc_value))
                clean.__dict__["exception.stacktrace"] = clean.exc_text
        elif record.exc_text:
            clean.exc_text = redact_text(record.exc_text)
        return clean


def _format_exception(exc_info: Any) -> str:  # noqa: ANN401
    """The traceback text of an `exc_info` triple, as `Formatter.formatException` renders it."""
    return logging.Formatter().formatException(exc_info)


class _JSONFormatter(logging.Formatter):
    """Renders one JSON object per line: timestamp, level, logger, message, extras."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record as a single-line JSON object."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = redact_value(key, value)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = trace.format_trace_id(span_context.trace_id)
            payload["span_id"] = trace.format_span_id(span_context.span_id)
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_info is not None:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        elif record.exc_text:
            payload["exception"] = redact_text(record.exc_text)
        return json.dumps(payload, default=str)


_OWNED = "_craftainer_observability"
"""Attribute set on the handlers `configure_logging` installs, to find them on a re-call."""

_providers: dict[str, LoggerProvider] = {}
"""The process-wide OTLP log provider, under `"logs"` once created."""


def _drop_owned_handlers(root: logging.Logger) -> None:
    """Remove the handlers an earlier `configure_logging` call put on `root`."""
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)


def configure_logging() -> None:
    """Attach a JSON stdout handler to the root logger, plus OTLP export if configured.

    Idempotent: a second call replaces the handlers the first installed instead of
    stacking another set (which would print every line twice). The OTLP log provider is
    created once per process and reused, so the exporter settings of the first call that
    enables export win.
    """
    root = logging.getLogger()
    _drop_owned_handlers(root)
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    handler.addFilter(RedactingFilter())
    setattr(handler, _OWNED, True)
    root.addHandler(handler)
    # uvicorn configures its own plain-text handlers (propagate=False) before importing
    # the app; hand its loggers over to the root handlers so they are JSON too.
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        if (
            name == "uvicorn.access"
            and not uvicorn_logger.handlers
            and not uvicorn_logger.propagate
        ):
            # uvicorn's `--no-access-log` / UVICORN_ACCESS_LOG=false leaves exactly this
            # state (no handlers, propagate=False); re-routing it would re-enable the lines.
            continue
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    if otlp_enabled() and exporter_enabled("logs"):
        _configure_otel_logging(root)


def _configure_otel_logging(root: logging.Logger) -> None:
    """Bridge the root logger to an OTLP log exporter."""
    provider = _providers.get("logs")
    if provider is None:
        provider = LoggerProvider(resource=shared_resource())
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(provider)
        _providers["logs"] = provider
    otel_handler = LoggingHandler(logger_provider=provider)
    otel_handler.addFilter(RedactingFilter(otel_exception_attributes=True))
    setattr(otel_handler, _OWNED, True)
    root.addHandler(otel_handler)
