"""Unit test: structured JSON logging, and the optional OTLP export bridge."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest
from opentelemetry.instrumentation.logging.handler import LoggingHandler

from craftainer_tool_observability.logs import _JSONFormatter, configure_logging
from craftainer_tool_observability.middleware import _request_id


@pytest.fixture
def _clean_root_handlers() -> Iterator[None]:
    """Save/restore the root logger's handlers -- it's shared process-wide state."""
    original = list(logging.getLogger().handlers)
    yield
    logging.getLogger().handlers = original


def test_json_formatter_renders_core_fields() -> None:
    """_JSONFormatter renders timestamp/level/logger/message as one JSON object."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
    payload = json.loads(_JSONFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"


def test_json_formatter_includes_extra_fields() -> None:
    """_JSONFormatter includes fields passed via logging's `extra=` kwarg."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
    record.request_id = "abc123"
    payload = json.loads(_JSONFormatter().format(record))
    assert payload["request_id"] == "abc123"


@pytest.mark.parametrize(
    "key", ["token", "access_token", "password", "client_secret", "Authorization", "claims"]
)
def test_json_formatter_redacts_credential_shaped_extra_fields(key: str) -> None:
    """An extra= field whose key looks like a credential is redacted, not logged verbatim."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
    setattr(record, key, "super-secret-value")
    payload = json.loads(_JSONFormatter().format(record))
    assert payload[key] == "[REDACTED]"
    assert "super-secret-value" not in json.dumps(payload)


def test_json_formatter_includes_exception_info() -> None:
    """_JSONFormatter renders exception info when the record carries it."""
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    payload = json.loads(_JSONFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_attaches_json_handler_to_root(_clean_root_handlers: None) -> None:
    """configure_logging() always attaches a JSON-formatting stdout handler."""
    configure_logging()
    root = logging.getLogger()
    assert any(isinstance(h.formatter, _JSONFormatter) for h in root.handlers)


def test_configure_logging_adds_otel_handler_when_endpoint_set(
    _clean_root_handlers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """configure_logging() bridges to OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    configure_logging()
    root = logging.getLogger()
    assert any(isinstance(h, LoggingHandler) for h in root.handlers)


def test_json_formatter_includes_request_id_when_set() -> None:
    """A request ID in the ContextVar appears as `request_id`; absent otherwise."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", (), None)
    assert "request_id" not in json.loads(_JSONFormatter().format(record))
    token = _request_id.set("req-1")
    try:
        payload = json.loads(_JSONFormatter().format(record))
    finally:
        _request_id.reset(token)
    assert payload["request_id"] == "req-1"


@pytest.fixture
def _restore_uvicorn_loggers() -> Iterator[None]:
    """Save/restore uvicorn's loggers' handlers and propagate flags (process-wide state)."""
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        n: (list(logging.getLogger(n).handlers), logging.getLogger(n).propagate) for n in names
    }
    yield
    for n, (handlers, propagate) in saved.items():
        logging.getLogger(n).handlers = handlers
        logging.getLogger(n).propagate = propagate


@pytest.mark.usefixtures("_clean_root_handlers", "_restore_uvicorn_loggers")
def test_configure_logging_routes_uvicorn_loggers_to_root() -> None:
    """With the access log on, uvicorn's own handlers are dropped and its loggers propagate."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers = [logging.NullHandler()]
        uv.propagate = False
    configure_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate is True


@pytest.mark.usefixtures("_clean_root_handlers", "_restore_uvicorn_loggers")
def test_configure_logging_keeps_disabled_uvicorn_access_log_disabled() -> None:
    """`--no-access-log` (no handlers, propagate=False) must stay exactly that way."""
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    configure_logging()
    assert access.handlers == []
    assert access.propagate is False
