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

_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)

# _JSONFormatter.format below passes every non-reserved LogRecord attribute through
# verbatim, so a future `logger.info(..., extra={"token": ...})` (or "password"/
# "authorization"/"claims"/etc.) would otherwise leak that value into stdout/OTLP as
# plain text. A substring match, not an exact-name allowlist, so a differently-cased
# or prefixed key (e.g. "access_token", "Authorization") is caught too.
_REDACTED_EXTRA_KEY_SUBSTRINGS = (
    "token",
    "password",
    "secret",
    "authorization",
    "claims",
    "credential",
)


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


def _redact_extra(key: str, value: Any) -> Any:  # noqa: ANN401
    """Replace `value` with a placeholder if `key` looks like it holds a credential."""
    lowered = key.lower()
    if any(substring in lowered for substring in _REDACTED_EXTRA_KEY_SUBSTRINGS):
        return "[REDACTED]"
    return value


class _JSONFormatter(logging.Formatter):
    """Renders one JSON object per line: timestamp, level, logger, message, extras."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record as a single-line JSON object."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS:
                payload[key] = _redact_extra(key, value)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = trace.format_trace_id(span_context.trace_id)
            payload["span_id"] = trace.format_span_id(span_context.span_id)
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Attach a JSON stdout handler to the root logger, plus OTLP export if configured."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
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
    provider = LoggerProvider(resource=shared_resource())
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    root.addHandler(LoggingHandler(logger_provider=provider))
