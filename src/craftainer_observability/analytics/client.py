"""`track()` and the bounded, fire-and-forget delivery queue."""

import atexit
import logging
import os
import queue
import threading
import time
from collections.abc import Mapping
from typing import Any

import httpx

from craftainer_observability.analytics.adapters import (
    BATCH_SIZE,
    Adapter,
    Event,
    LogAdapter,
    NoneAdapter,
    OtelAdapter,
    UmamiAdapter,
)
from craftainer_observability.analytics.config import AnalyticsConfig
from craftainer_observability.analytics.context import get_request_context
from craftainer_observability.analytics.validate import clean_props, clean_url, valid_name

logger = logging.getLogger(__name__)

QUEUE_CAPACITY = 1000
FLUSH_INTERVAL_SECONDS = 5.0


class AnalyticsClient:
    """Validates events and hands them to an adapter without ever raising or blocking.

    In-process adapters (`log`, `otel`) are called inline. The `umami` adapter is fed
    through a queue of `QUEUE_CAPACITY` events drained by a daemon thread every
    `FLUSH_INTERVAL_SECONDS`; an event that arrives while the queue is full is dropped.
    """

    def __init__(
        self,
        config: AnalyticsConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        """Build the adapter for `config.backend` (falling back to `none` if misconfigured)."""
        self.config = config
        self._flush_interval = flush_interval
        self._queue: queue.Queue[Event] = queue.Queue(QUEUE_CAPACITY)
        self._drain_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._adapter = self._build_adapter(transport)
        self._queued = isinstance(self._adapter, UmamiAdapter)

    def _reset_after_fork(self) -> None:
        """Drop state inherited from the parent: its flusher thread does not exist here."""
        self._queue = queue.Queue(QUEUE_CAPACITY)
        self._drain_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _build_adapter(self, transport: httpx.BaseTransport | None) -> Adapter:
        cfg = self.config
        if not cfg.enabled or cfg.backend == "none":
            return NoneAdapter()
        if cfg.backend == "log":
            return LogAdapter()
        if cfg.backend == "otel":
            return OtelAdapter()
        if not (cfg.endpoint and cfg.site_id):
            logger.warning("ANALYTICS_BACKEND=umami needs ANALYTICS_ENDPOINT and SITE_ID; off")
            return NoneAdapter()
        return UmamiAdapter(
            endpoint=cfg.endpoint,
            site_id=cfg.site_id,
            hostname=cfg.hostname,
            transport=transport,
        )

    def _allowed_by_request(self) -> bool:
        ctx = get_request_context()
        if ctx is None:
            return self.config.consent != "opt-in"
        if self.config.respect_gpc and ctx.gpc:
            return False
        return not (self.config.consent == "opt-in" and not ctx.consent)

    def track(
        self,
        name: str,
        props: Mapping[str, Any] | None = None,
        *,
        url: str | None = None,
    ) -> None:
        """Record one event. Invalid input and any failure are dropped, never raised."""
        try:
            self._track(name, props, url)
        except Exception:
            logger.debug("analytics track failed", exc_info=True)

    def _track(self, name: str, props: Mapping[str, Any] | None, url: str | None) -> None:
        if isinstance(self._adapter, NoneAdapter) or not self._allowed_by_request():
            return
        if not valid_name(name):
            logger.warning("analytics event dropped: invalid name")
            return
        event = Event(
            name=name,
            props=clean_props(props, self.config.prop_keys),
            url=clean_url(url),
            timestamp=int(time.time()),
        )
        if not self._queued:
            self._adapter.send([event])
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.debug("analytics queue full; event dropped")
            return
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        with self._start_lock:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(
                    target=self._run, name="analytics-flush", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self.flush()
        self.flush()

    def flush(self) -> None:
        """Deliver everything queued now; failures are logged, never raised."""
        with self._drain_lock:
            events: list[Event] = []
            while True:
                try:
                    events.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            for start in range(0, len(events), BATCH_SIZE):
                try:
                    self._adapter.send(events[start : start + BATCH_SIZE])
                except Exception as exc:
                    logger.debug("analytics delivery failed: %s", type(exc).__name__)
                    break  # backend unreachable: drop the rest rather than wait out every timeout

    def shutdown(self) -> None:
        """Stop the flush thread after a final flush and close the HTTP client."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self.flush()
        if isinstance(self._adapter, UmamiAdapter):
            self._adapter.close()


_client: AnalyticsClient | None = None
_client_lock = threading.Lock()
_atexit_registered = False


def get_client() -> AnalyticsClient:
    """The process-wide client, built from the environment on first use."""
    global _client, _atexit_registered  # noqa: PLW0603
    with _client_lock:
        if _client is None:
            _client = AnalyticsClient(AnalyticsConfig.from_env())
            if not _atexit_registered:
                atexit.register(shutdown)
                _atexit_registered = True
        return _client


def _reset_after_fork() -> None:
    """Child side of `fork()`: fresh locks and queue; the flusher restarts on the next `track()`."""
    global _client_lock  # noqa: PLW0603
    _client_lock = threading.Lock()
    if _client is not None:
        _client._reset_after_fork()


os.register_at_fork(after_in_child=_reset_after_fork)


def track(name: str, props: Mapping[str, Any] | None = None, *, url: str | None = None) -> None:
    """Record an analytics event (contract I3). Never raises, never blocks the caller."""
    try:
        get_client().track(name, props, url=url)
    except Exception:
        logger.debug("analytics track failed", exc_info=True)


def shutdown() -> None:
    """Flush and discard the process-wide client; the next `track()` re-reads the environment."""
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.shutdown()
