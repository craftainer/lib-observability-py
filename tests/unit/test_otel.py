"""Unit test: OTel tracer/meter setup, instrumentation, and trace-id log correlation.

OTel's global providers are set-once per process, so these tests never touch them:
they inject in-memory-exporter providers into `otel._state` (which `instrument_*` read)
and pass nothing through the globals. `configure_telemetry` itself is checked with the
global setters monkeypatched.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from craftainer_observability import otel
from craftainer_observability.logs import (
    _JSONFormatter,
    exporter_enabled,
    otlp_enabled,
    shared_resource,
)


class _Providers:
    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.tracer_provider = TracerProvider(resource=shared_resource())
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.reader = InMemoryMetricReader()
        self.meter_provider = MeterProvider(metric_readers=[self.reader])


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Providers]:
    """In-memory providers installed as the module's configured telemetry."""
    p = _Providers()
    monkeypatch.setattr(otel, "_state", otel._Telemetry(p.tracer_provider, p.meter_provider))
    monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
    yield p
    SQLAlchemyInstrumentor().uninstrument()


@pytest.fixture(autouse=True)
def _fresh_resource() -> Iterator[None]:
    shared_resource.cache_clear()
    yield
    shared_resource.cache_clear()


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_request_produces_server_span_and_health_is_excluded(providers: _Providers) -> None:
    """A request yields a server span and request metrics; /health/* yields neither."""
    app = _app()
    otel.instrument_app(app)
    client = TestClient(app)
    assert client.get("/health/live").status_code == 200
    assert not providers.spans.get_finished_spans()
    assert client.get("/ping").status_code == 200
    server = [s for s in providers.spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert [s.attributes["http.route"] for s in server if s.attributes] == ["/ping"]
    data = providers.reader.get_metrics_data()
    names = {
        m.name
        for rm in (data.resource_metrics if data else [])
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    # Stable `http.server.request.duration` needs OTEL_SEMCONV_STABILITY_OPT_IN=http.
    assert {"http.server.active_requests", "http.server.duration"} <= names


def test_excluded_urls_env_overrides_default(
    providers: _Providers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set OTEL_PYTHON_FASTAPI_EXCLUDED_URLS replaces the /health default."""
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "ping")
    app = _app()
    otel.instrument_app(app)
    client = TestClient(app)
    client.get("/ping")
    assert not providers.spans.get_finished_spans()
    client.get("/health/live")
    assert providers.spans.get_finished_spans()


def test_query_produces_db_span(providers: _Providers) -> None:
    """A query on an instrumented engine yields a client span."""
    engine = create_engine("sqlite://")
    fake = cast("AsyncEngine", type("E", (), {"sync_engine": engine})())
    otel.instrument_engine(fake)
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    spans = providers.spans.get_finished_spans()
    assert any(s.attributes and s.attributes.get("db.system") == "sqlite" for s in spans)
    engine.dispose()


def test_instrument_is_noop_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no configured telemetry nothing is instrumented."""
    monkeypatch.setattr(otel, "_state", None)
    app = _app()
    otel.instrument_app(app)
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
    engine = create_engine("sqlite://")
    otel.instrument_engine(cast("AsyncEngine", type("E", (), {"sync_engine": engine})()))
    assert not SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry


def test_log_lines_carry_trace_id_only_inside_a_span(providers: _Providers) -> None:
    """JSON log lines get trace_id/span_id while a span is active."""
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hi", (), None)
    assert "trace_id" not in json.loads(_JSONFormatter().format(record))
    with providers.tracer_provider.get_tracer("t").start_as_current_span("s") as span:
        payload = json.loads(_JSONFormatter().format(record))
        ctx = span.get_span_context()
    assert payload["trace_id"] == trace.format_trace_id(ctx.trace_id)
    assert payload["span_id"] == trace.format_span_id(ctx.span_id)


def test_configure_telemetry_is_noop_without_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset OTEL_EXPORTER_OTLP_ENDPOINT means nothing is set up."""
    monkeypatch.setattr(otel, "_state", None)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    otel.configure_telemetry()
    assert otel._state is None


def test_configure_telemetry_is_noop_when_sdk_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """OTEL_SDK_DISABLED=true wins over a set endpoint."""
    monkeypatch.setattr(otel, "_state", None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    otel.configure_telemetry()
    assert otel._state is None
    assert not otlp_enabled()


def test_configure_telemetry_sets_providers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """With an endpoint set, providers are built once, share a Resource, and re-calls no-op."""
    monkeypatch.setattr(otel, "_state", None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    set_calls: list[Any] = []
    monkeypatch.setattr(trace, "set_tracer_provider", set_calls.append)
    monkeypatch.setattr(metrics, "set_meter_provider", set_calls.append)
    otel.configure_telemetry()
    state = otel._state
    assert state is not None
    otel.configure_telemetry()
    assert otel._state is state
    assert len(set_calls) == 2
    assert state.tracer_provider.resource.attributes["service.version"] == "1.2.3"
    assert state.meter_provider._sdk_config.resource is state.tracer_provider.resource
    state.tracer_provider.shutdown()
    state.meter_provider.shutdown()


def test_per_signal_exporter_none_adds_no_processor_or_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTEL_TRACES_EXPORTER=none / OTEL_METRICS_EXPORTER=none switch the signal off."""
    monkeypatch.setattr(otel, "_state", None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setattr(trace, "set_tracer_provider", lambda _p: None)
    monkeypatch.setattr(metrics, "set_meter_provider", lambda _p: None)

    def _fail(*_a: object, **_k: object) -> None:
        raise AssertionError("exporter must not be built")

    monkeypatch.setattr(otel, "BatchSpanProcessor", _fail)
    monkeypatch.setattr(otel, "PeriodicExportingMetricReader", _fail)
    otel.configure_telemetry()
    assert otel._state is not None
    assert not exporter_enabled("traces")
    assert not exporter_enabled("metrics")


def test_service_version_from_app_version_unless_resource_attributes_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """APP_VERSION fills service.version; OTEL_RESOURCE_ATTRIBUTES wins; name from env."""
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("APP_VERSION", "2.0.0")
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    assert shared_resource().attributes["service.version"] == "2.0.0"
    assert shared_resource().attributes["service.name"] == "svc"
    shared_resource.cache_clear()
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.version=9.9.9")
    assert shared_resource().attributes["service.version"] == "9.9.9"
