"""Unit test: RequestIDMiddleware (read/create/validate/echo, ContextVar, span attribute)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from starlette.types import Receive, Scope, Send

from craftainer_observability.middleware import RequestIDMiddleware, get_request_id


def _client(header_name: str | None = None) -> TestClient:
    """A standalone app echoing the request ID the handler sees."""
    app = FastAPI()

    @app.get("/id")
    async def read_id() -> dict[str, str | None]:
        return {"id": get_request_id()}

    if header_name is None:
        app.add_middleware(RequestIDMiddleware)
    else:
        app.add_middleware(RequestIDMiddleware, header_name=header_name)
    return TestClient(app)


def test_valid_incoming_id_is_kept_and_echoed() -> None:
    """A well-formed X-Request-ID is visible to the handler and echoed back."""
    response = _client().get("/id", headers={"X-Request-ID": "abc-123_.X"})
    assert response.json() == {"id": "abc-123_.X"}
    assert response.headers["x-request-id"] == "abc-123_.X"


def test_missing_id_is_generated() -> None:
    """Without the header a 32-character hex ID is created and echoed."""
    response = _client().get("/id")
    generated = response.headers["x-request-id"]
    assert len(generated) == 32
    assert response.json() == {"id": generated}


@pytest.mark.parametrize("bad", ["a b", "x" * 129, "id;rm", "a\x7fb", ""])
def test_invalid_incoming_id_is_replaced(bad: str) -> None:
    """Wrong charset, too long or empty IDs are replaced, never echoed."""
    response = _client().get("/id", headers={"X-Request-ID": bad})
    assert response.headers["x-request-id"] != bad
    assert len(response.headers["x-request-id"]) == 32


def test_custom_header_name() -> None:
    """header_name changes both the request and the response header."""
    response = _client("X-Correlation-ID").get("/id", headers={"X-Correlation-ID": "c1"})
    assert response.headers["x-correlation-id"] == "c1"
    assert "x-request-id" not in response.headers


def test_context_is_reset_after_request() -> None:
    """The ContextVar does not leak out of the request."""
    _client().get("/id")
    assert get_request_id() is None


def test_response_header_is_not_duplicated() -> None:
    """A handler-set header of the same name is replaced by the request's ID."""
    app = FastAPI()

    @app.get("/dup")
    async def dup() -> dict[str, str]:
        return {}

    @app.middleware("http")
    async def set_header(request, call_next):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers["X-Request-ID"] = "from-inner"
        return response

    app.add_middleware(RequestIDMiddleware)
    response = TestClient(app).get("/dup", headers={"X-Request-ID": "mine"})
    assert response.headers.get_list("x-request-id") == ["mine"]


async def test_non_http_scope_passes_through() -> None:
    """A lifespan scope reaches the wrapped app untouched."""
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> dict[str, str]:
        return {}

    async def send(_message: object) -> None:
        return None

    await RequestIDMiddleware(inner)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]


def test_id_is_set_on_recording_span() -> None:
    """With a recording span active, the ID lands on it as the semconv header attribute."""
    tracer = TracerProvider().get_tracer("test")
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {}

    with tracer.start_as_current_span("server") as span, TestClient(app) as client:
        client.get("/x", headers={"X-Request-ID": "span-id"})
        attributes = dict(span.attributes or {})  # type: ignore[attr-defined]
    assert attributes["http.request.header.x-request-id"] == ("span-id",)
