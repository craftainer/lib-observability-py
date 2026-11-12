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

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.util import BoundedList
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import Attributes, AttributeValue
from sqlalchemy.ext.asyncio import AsyncEngine

from craftainer_observability.logs import exporter_enabled, otlp_enabled, shared_resource
from craftainer_observability.redaction import REDACTED, is_sensitive_key, redact_text

logger = logging.getLogger(__name__)

# Probes would otherwise flood traces; used only when the standard variable is unset.
DEFAULT_EXCLUDED_URLS = "/health/.*"


_URL_ATTRIBUTES = frozenset({"http.url", "url.full", "http.target", "url.path"})
"""Span attributes the ASGI instrumentation fills from the request; the query is cut."""
_QUERY_ATTRIBUTES = frozenset({"url.query"})
"""Span attributes that are the query string itself; dropped."""


def _strip_query(value: str) -> str:
    """`value` (a URL or a request target) without its query string and fragment."""
    if "?" not in value and "#" not in value:
        return value
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:  # e.g. an unbalanced `[` from a crafted request target
        return value.split("?", 1)[0].split("#", 1)[0]


def _redact_attributes(attributes: Attributes) -> Attributes:
    """Attributes with the request query stripped and credential keys and values hidden."""
    if not attributes:
        return attributes
    clean: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if key in _QUERY_ATTRIBUTES:
            continue
        if key in _URL_ATTRIBUTES and isinstance(value, str):
            value = _strip_query(value)  # noqa: PLW2901
        if is_sensitive_key(key):
            clean[key] = REDACTED
        elif isinstance(value, str):
            clean[key] = redact_text(value)
        elif isinstance(value, Sequence) and not isinstance(value, bytes):
            clean[key] = cast(
                "AttributeValue",
                [redact_text(item) if isinstance(item, str) else item for item in value],
            )
        else:
            clean[key] = value
    return clean


def _bounded(attributes: Attributes, dropped: int) -> Attributes:
    """`attributes` as a `BoundedAttributes` that keeps the original dropped count."""
    bounded = BoundedAttributes(None, attributes, immutable=True)
    bounded.dropped = dropped
    return bounded


def _redact_span(span: ReadableSpan) -> ReadableSpan:
    """A copy of `span` safe to export: name, attributes, events, links and status redacted.

    The dropped-attribute, -event and -link counts are carried over. A span that cannot be
    redacted is exported with its name, attributes, events and links removed, never as-is.
    """
    try:
        events = BoundedList.from_seq(
            None,
            [
                Event(
                    redact_text(event.name),
                    _bounded(_redact_attributes(event.attributes), event.dropped_attributes),
                    event.timestamp,
                )
                for event in span.events
            ],
        )
        events.dropped = span.dropped_events
        links = BoundedList.from_seq(
            None,
            [
                Link(
                    link.context,
                    _bounded(_redact_attributes(link.attributes), link.dropped_attributes),
                )
                for link in span.links
            ],
        )
        links.dropped = span.dropped_links
        status = span.status
        if status.description:
            status = Status(status.status_code, redact_text(status.description))
        name = redact_text(span.name)
        attributes = _bounded(_redact_attributes(span.attributes), span.dropped_attributes)
    except Exception:  # fail closed: a span we cannot scrub must not go out as it is
        logger.exception("Span redaction failed; exporting the span without its content")
        name, attributes = REDACTED, None
        events, links = BoundedList[Event](None), BoundedList[Link](None)
        status = Status(span.status.status_code)
    return ReadableSpan(
        name=name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(SpanExporter):
    """Wraps a span exporter so nothing it sends carries a credential or a request query.

    Applies the shared key list (`redaction.REDACTION_KEYS`) to attribute keys and the
    shared text patterns to attribute values, exception events and the status
    description, and removes the query string from the ASGI URL attributes
    (`http.url`, `url.full`, `http.target`, `url.path`; `url.query` is dropped), so
    `/x?token=abc` is exported as `/x`.
    """

    def __init__(self, inner: SpanExporter) -> None:
        """Wrap `inner`."""
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export redacted copies of `spans`."""
        return self._inner.export([_redact_span(span) for span in spans])

    def shutdown(self) -> None:
        """Shut `inner` down."""
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush `inner`."""
        return self._inner.force_flush(timeout_millis)


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
        tracer_provider.add_span_processor(
            BatchSpanProcessor(RedactingSpanExporter(OTLPSpanExporter()))
        )
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
