"""Unit test: structured JSON logging, and the optional OTLP export bridge."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest
from opentelemetry.instrumentation.logging.handler import LoggingHandler

from app.telemetry import _JSONFormatter, configure_logging


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
