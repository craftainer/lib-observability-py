"""Optional OpenTelemetry traces and metrics, pushed over OTLP/HTTP.

Same on/off switch as the log export in `logs.py`: nothing is set up unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set (and `OTEL_SDK_DISABLED` isn't `true`). OTel's own
standard environment variables are the only configuration (ADR-0007), never re-modeled
as application settings. The SDK reads these itself when the providers are built:
`OTEL_TRACES_SAMPLER(_ARG)`, `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_SERVICE_NAME`,
`OTEL_RESOURCE_ATTRIBUTES` and the exporters' endpoint/headers/protocol variables. What it
doesn't read when providers are built by hand is handled here: the per-signal
`OTEL_TRACES_EXPORTER`/`OTEL_METRICS_EXPORTER` (`none`) and `OTEL_SDK_DISABLED`.

The tracer and meter providers share `logs.shared_resource()` with the logger provider, so
all three signals carry the same `service.name`/`service.version`.

The SQLAlchemy instrumentation already emits connection-pool usage
(`db.client.connections.usage`, with `state` of `idle`/`used`), so no extra pool gauges.
This module imports nothing from `app`.
"""

import os
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

from craftainer_observability.logs import exporter_enabled, otlp_enabled, shared_resource

# Probes would otherwise flood traces; used only when the standard variable is unset.
DEFAULT_EXCLUDED_URLS = "/health/.*"


@dataclass(frozen=True)
class _Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider


_state: _Telemetry | None = None


def configure_telemetry() -> None:
    """Set up the tracer and meter providers if OTLP is configured; idempotent, else a no-op."""
    global _state  # noqa: PLW0603 -- module-level set-once guard, same as OTel's own globals
    if _state is not None or not otlp_enabled():
        return
    resource = shared_resource()
    tracer_provider = TracerProvider(resource=resource)
    if exporter_enabled("traces"):
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    readers: list[MetricReader] = []
    if exporter_enabled("metrics"):
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    _state = _Telemetry(tracer_provider, meter_provider)


def instrument_app(app: FastAPI) -> None:
    """Add server spans and request metrics to `app`; a no-op unless telemetry is configured."""
    if _state is None:
        return
    excluded = os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") or DEFAULT_EXCLUDED_URLS
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_state.tracer_provider,
        meter_provider=_state.meter_provider,
        excluded_urls=excluded,
    )


def instrument_engine(engine: AsyncEngine) -> None:
    """Add query spans and pool metrics to `engine`; a no-op unless telemetry is configured."""
    if _state is None:
        return
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=_state.tracer_provider,
        meter_provider=_state.meter_provider,
    )
