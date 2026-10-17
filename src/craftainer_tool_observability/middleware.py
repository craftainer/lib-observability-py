"""Request-ID middleware (pure ASGI), plus the ContextVar the log formatter reads.

Reads the incoming request-ID header (`X-Request-ID` by default) or creates a UUID4 hex
value, validates it (1-128 characters from `[A-Za-z0-9._-]`, so a client can't inject
newlines or other control characters into log lines) and replaces an invalid one with a
fresh ID. The ID is echoed on the response, stored in a ContextVar for `get_request_id()`
(and `logs.py`'s JSON formatter) and, when OpenTelemetry is importable and a span is
recording, set on that span as the semconv generic request-header attribute
`http.request.header.<header name, lowercased>` (a string array).
Install it inside the OTel server instrumentation so the server span is already active.
This module imports nothing from `app`, and nothing from OTel at module level.
"""

import re
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_HEADER_NAME = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """The current request's ID, or None outside a request."""
    return _request_id.get()


def _set_span_attribute(header_name: str, request_id: str) -> None:
    """Attach the ID to the recording span, if OpenTelemetry is installed."""
    try:
        from opentelemetry import trace  # noqa: PLC0415 # optional dependency, no hard import
    except ImportError:  # pragma: no cover -- OTel is installed in every test environment
        return
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(f"http.request.header.{header_name.lower()}", [request_id])


class RequestIDMiddleware:
    """Pure ASGI middleware: read or create, validate, echo and propagate a request ID."""

    def __init__(self, app: ASGIApp, header_name: str = DEFAULT_HEADER_NAME) -> None:
        """Wrap `app`; `header_name` is the request and response header carrying the ID."""
        self.app = app
        self.header_name = header_name
        self._raw_name = header_name.lower().encode("latin-1")

    def _incoming_id(self, scope: Scope) -> str:
        """The client's ID if it is valid, otherwise a freshly generated one."""
        for name, value in scope["headers"]:
            if name == self._raw_name:
                candidate: str = value.decode("latin-1")
                if _VALID_REQUEST_ID.fullmatch(candidate):
                    return candidate
                break
        return uuid.uuid4().hex

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle HTTP and WebSocket scopes; pass anything else (lifespan) straight through."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = self._incoming_id(scope)
        token = _request_id.set(request_id)
        _set_span_attribute(self.header_name, request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[Any] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != self._raw_name
                ]
                headers.append((self._raw_name, request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)
