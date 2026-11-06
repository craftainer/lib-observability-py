"""Unit test: structured JSON logging, and the optional OTLP export bridge."""

import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.instrumentation.logging.handler import LoggingHandler

from craftainer_observability import logs as logs_mod
from craftainer_observability.logs import RedactingFilter, _JSONFormatter, configure_logging
from craftainer_observability.middleware import _request_id


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


def test_configure_logging_is_idempotent(_clean_root_handlers: None) -> None:
    """Calling configure_logging() twice leaves one JSON handler, not two."""
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    assert sum(isinstance(h.formatter, _JSONFormatter) for h in root.handlers) == 1


def test_configure_logging_keeps_foreign_handlers(_clean_root_handlers: None) -> None:
    """A handler the host added itself survives a configure_logging() call."""
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    configure_logging()
    configure_logging()
    assert foreign in logging.getLogger().handlers


def test_configure_logging_does_not_stack_otel_handlers(
    _clean_root_handlers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With OTLP on, a re-call keeps one OTEL handler and reuses the provider."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    configure_logging()
    first = [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]
    configure_logging()
    second = [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]
    assert len(first) == len(second) == 1
    assert first[0]._logger_provider is second[0]._logger_provider


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


def _raise_with_secret() -> Any:  # noqa: ANN401
    """An `exc_info` triple for an error whose text embeds a DSN."""
    try:
        raise ValueError("connect failed: postgres://app:hunter2@db/x")
    except ValueError:
        return sys.exc_info()


def _record_with_secrets() -> logging.LogRecord:
    """A record with secrets in the message args, extras, stack info and exception."""
    record = logging.LogRecord(
        "app.test", logging.ERROR, __file__, 1, "login %s", ("token=abc123",), _raise_with_secret()
    )
    record.__dict__["api_key"] = "hunter2"
    record.note = "api_key=zzz"
    record.stack_info = "stack password=hunter2"
    return record


def test_filter_redacts_message_extras_stack_and_exception_without_touching_original() -> None:
    """The redacting filter returns a clean copy and leaves the shared record alone."""
    record = _record_with_secrets()
    clean = RedactingFilter().filter(record)
    assert isinstance(clean, logging.LogRecord)
    dumped = json.dumps(clean.__dict__, default=str)
    for secret in ("abc123", "hunter2", "zzz"):
        assert secret not in dumped
    assert clean.exc_info is None
    assert clean.exc_text is not None
    assert "ValueError" in clean.exc_text
    assert "exception.stacktrace" not in clean.__dict__
    assert record.exc_info is not None
    assert record.__dict__["api_key"] == "hunter2"


def test_filter_adds_redacted_otel_exception_attributes() -> None:
    """For the OTLP handler the filter supplies exception.* attributes, redacted."""
    clean = RedactingFilter(otel_exception_attributes=True).filter(_record_with_secrets())
    assert isinstance(clean, logging.LogRecord)
    assert clean.__dict__["exception.type"] == "ValueError"
    assert "hunter2" not in clean.__dict__["exception.message"]
    assert "hunter2" not in clean.__dict__["exception.stacktrace"]
    assert "postgres://[REDACTED]@db/x" in clean.__dict__["exception.message"]


def test_filter_redacts_an_already_formatted_exc_text() -> None:
    """A record whose traceback was formatted earlier (exc_text, no exc_info) is scrubbed."""
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "x", (), None)
    record.exc_text = "Traceback ... password=hunter2"
    clean = RedactingFilter().filter(record)
    assert isinstance(clean, logging.LogRecord)
    assert clean.exc_text == "Traceback ... password=[REDACTED]"
    payload = json.loads(_JSONFormatter().format(clean))
    assert payload["exception"] == "Traceback ... password=[REDACTED]"


def test_json_formatter_redacts_message_and_exception_on_its_own() -> None:
    """Even without the filter the formatter redacts the message and the traceback."""
    payload = json.loads(_JSONFormatter().format(_record_with_secrets()))
    assert "hunter2" not in json.dumps(payload)
    assert "abc123" not in json.dumps(payload)
    assert "ValueError" in payload["exception"]


@pytest.mark.usefixtures("_clean_root_handlers")
def test_configure_logging_filters_every_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the stdout and the OTLP bridge handler carry a RedactingFilter."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    configure_logging()
    owned = [h for h in logging.getLogger().handlers if getattr(h, logs_mod._OWNED, False)]
    assert len(owned) == 2
    assert all(any(isinstance(f, RedactingFilter) for f in h.filters) for h in owned)
    otel_handler = next(h for h in owned if isinstance(h, LoggingHandler))
    exported: list[logging.LogRecord] = []
    monkeypatch.setattr(otel_handler, "emit", exported.append)
    logging.getLogger("app.otlp").error("x %s", "password=hunter2", exc_info=_raise_with_secret())
    assert "hunter2" not in json.dumps([r.__dict__ for r in exported], default=str)


def test_filter_passes_a_plain_record_through_redacted() -> None:
    """A record with no exception and no cached traceback just gets its message scrubbed."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "token=%s", ("abc",), None)
    clean = RedactingFilter().filter(record)
    assert isinstance(clean, logging.LogRecord)
    assert clean.getMessage() == "token=[REDACTED]"
    assert clean.exc_text is None
    assert clean.args is None


def test_filter_never_raises_and_hides_everything_when_redaction_fails() -> None:
    """A bad %-format must not escape `logger.info`; the record is replaced by a blank one."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "n=%d", ("x",), None)
    record.__dict__["api_note"] = "keep? no"
    record.stack_info = "password=hunter2"
    clean = RedactingFilter().filter(record)
    assert isinstance(clean, logging.LogRecord)
    assert clean.getMessage() == "[REDACTED]"
    assert clean.__dict__["api_note"] == "[REDACTED]"
    assert clean.stack_info is None
    assert clean.exc_info is None


def test_formatter_with_redact_off_trusts_the_filter() -> None:
    """configure_logging's formatter does not redact twice; the filter has already."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "password=hunter2", (), None)
    assert "hunter2" not in _JSONFormatter().format(record)
    assert "hunter2" in _JSONFormatter(redact=False).format(record)
    clean = RedactingFilter().filter(record)
    assert isinstance(clean, logging.LogRecord)
    assert "hunter2" not in _JSONFormatter(redact=False).format(clean)
