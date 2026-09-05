"""Structured stdout logging, optionally bridged to an OTEL collector.

Always logs structured JSON to stdout; additionally exports logs via OTLP when
OTEL_EXPORTER_OTLP_ENDPOINT is set. Logs only -- no tracing/metrics instrumentation,
since that's the only signal asked for. OTEL's own env vars (endpoint, headers,
protocol, compression, certificate) are read directly by OTLPLogExporter() itself,
not re-modeled on app.config.Settings -- OTEL's env-var convention is already the
single source of truth for those, vendor-neutral and unrelated to this app's own
settings.

`_JSONFormatter.format`/`_redact_extra` are `# pragma: no cover` for tests/e2e
specifically: app.oidc/app.problem_details/app.controllers.crud_actions do call
`logging.getLogger(...).warning/info/exception` on the real HTTP stack now, but
none of them pass a credential-shaped `extra=` key or (outside a genuine bug) an
unhandled exception, so `format`'s extra-field/exception branches and
`_redact_extra`'s redaction branch specifically never run through a live request.
tests/unit/test_telemetry.py exercises both directly and still counts toward its
own 95% gate.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from app.config import get_settings

settings = get_settings()

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


def _redact_extra(key: str, value: Any) -> Any:  # noqa: ANN401 # pragma: no cover -- see docstring
    """Replace `value` with a placeholder if `key` looks like it holds a credential."""
    lowered = key.lower()
    if any(substring in lowered for substring in _REDACTED_EXTRA_KEY_SUBSTRINGS):
        return "[REDACTED]"
    return value


class _JSONFormatter(logging.Formatter):
    """Renders one JSON object per line: timestamp, level, logger, message, extras."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover -- see module docstring
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

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):  # pragma: no cover -- see below
        _configure_otel_logging(root)


def _configure_otel_logging(root: logging.Logger) -> None:  # pragma: no cover
    """Bridge the root logger to an OTLP log exporter.

    `# pragma: no cover` above is for tests/e2e specifically: its live process
    never sets OTEL_EXPORTER_OTLP_ENDPOINT (see .devcontainer/compose.yml), so this
    branch can never run there -- tests/unit/test_telemetry.py exercises it
    directly (via monkeypatch.setenv) and still counts toward its own 95% gate.
    """
    provider = LoggerProvider(resource=Resource.create({"service.name": settings.app_name}))
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(provider)
    root.addHandler(LoggingHandler(logger_provider=provider))
