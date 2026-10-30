"""Backends: `none`, `log`, `otel` (in-process) and `umami` (HTTP batch)."""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from craftainer_observability.analytics.validate import Scalar

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
TIMEOUT_SECONDS = 2.0
USER_AGENT = "Mozilla/5.0 (compatible; craftainer-py)"
"""Umami silently drops events from bot-like user agents (e.g. httpx's default)."""
SERVER_IP = "server"
"""Constant `payload.ip` for backend events; never a raw client IP."""


@dataclass(frozen=True, slots=True)
class Event:
    """One validated event."""

    name: str
    props: dict[str, Scalar]
    url: str
    timestamp: int


class Adapter(Protocol):
    """Where validated events go. `send` may raise; the client catches it."""

    def send(self, events: list[Event]) -> None:
        """Deliver `events` (already validated)."""
        ...


class NoneAdapter:
    """Discards everything."""

    def send(self, events: list[Event]) -> None:
        """Drop `events`."""


class LogAdapter:
    """Writes one structured log line per event (`extra=` fields, JSON under `logs.py`)."""

    def __init__(self, logger_name: str = "craftainer_observability.analytics.events") -> None:
        """Log through `logger_name`."""
        self._logger = logging.getLogger(logger_name)

    def send(self, events: list[Event]) -> None:
        """Log each event at INFO."""
        for event in events:
            self._logger.info(
                "analytics_event",
                extra={
                    "analytics_event": event.name,
                    "analytics_props": event.props,
                    "analytics_url": event.url,
                },
            )


class OtelAdapter(LogAdapter):
    """Emits OTel log records through the stdlib bridge `configure_logging()` installs.

    With `[otel]` configured the root `LoggingHandler` exports these records over OTLP,
    with the shared resource and the active trace context.
    """

    def __init__(self) -> None:
        """Log through the dedicated `...analytics.otel` logger."""
        super().__init__("craftainer_observability.analytics.otel")


class UmamiAdapter:
    """Posts batches to Umami's `/api/batch`."""

    def __init__(
        self,
        *,
        endpoint: str,
        site_id: str,
        hostname: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Build the HTTP client (2 s timeout, bot-safe User-Agent)."""
        self._url = f"{endpoint.rstrip('/')}/api/batch"
        self._site_id = site_id
        self._hostname = hostname
        self._http = httpx.Client(
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def _item(self, event: Event) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "website": self._site_id,
            "url": event.url,
            "name": event.name,
            "ip": SERVER_IP,
            "timestamp": event.timestamp,
        }
        if self._hostname:
            payload["hostname"] = self._hostname
        if event.props:
            payload["data"] = event.props
        return {"type": "event", "payload": payload}

    def send(self, events: list[Event]) -> None:
        """POST `events` in chunks of at most 50; log (never raise on) a rejected batch."""
        for start in range(0, len(events), BATCH_SIZE):
            chunk = events[start : start + BATCH_SIZE]
            response = self._http.post(self._url, json=[self._item(e) for e in chunk])
            if response.status_code != httpx.codes.OK:
                logger.warning("umami batch rejected: HTTP %s", response.status_code)
                continue
            try:
                body = response.json()
                errors = body.get("errors", 0) if isinstance(body, dict) else 0
            except ValueError:
                logger.warning("umami batch reply was not JSON")
                continue
            if errors:
                logger.warning("umami batch reported %s errors of %s", errors, len(chunk))

    def close(self) -> None:
        """Close the HTTP client."""
        self._http.close()
