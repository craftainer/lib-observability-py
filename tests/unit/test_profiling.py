"""Unit test: install_profiling (disabled no-op, production refusal, ?profile=1 report)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from craftainer_observability.profiling import (
    ProfilingDisabledError,
    ProfilingMiddleware,
    install_profiling,
)


def _app() -> FastAPI:
    app = FastAPI()
    calls: list[int] = []

    @app.get("/work")
    async def work() -> dict[str, int]:
        calls.append(1)
        return {"calls": len(calls)}

    return app


def test_disabled_is_a_noop() -> None:
    """Disabled leaves the app alone, even with the query parameter."""
    app = _app()
    install_profiling(app, enabled=False, production=False)
    response = TestClient(app).get("/work?profile=1")
    assert response.json() == {"calls": 1}


def test_disabled_in_production_is_fine() -> None:
    """Production with profiling off does not raise."""
    install_profiling(_app(), enabled=False, production=True)


def test_enabled_in_production_raises() -> None:
    """Enabling profiling in production is refused."""
    with pytest.raises(ProfilingDisabledError):
        install_profiling(_app(), enabled=True, production=True)


def test_enabled_returns_html_report_only_when_flagged() -> None:
    """?profile=1 runs the request and answers with the pyinstrument HTML report."""
    app = _app()
    install_profiling(app, enabled=True, production=False)
    client = TestClient(app)
    profiled = client.get("/work?profile=1")
    assert profiled.status_code == 200
    assert profiled.headers["content-type"].startswith("text/html")
    assert "pyinstrument" in profiled.text.lower()
    assert client.get("/work").json() == {"calls": 2}
    assert client.get("/work?profile=0").json() == {"calls": 3}


def test_custom_query_param() -> None:
    """query_param renames the trigger."""
    app = _app()
    install_profiling(app, enabled=True, production=False, query_param="prof")
    assert TestClient(app).get("/work?prof=1").headers["content-type"].startswith("text/html")


async def test_non_http_scope_passes_through() -> None:
    """A lifespan scope is not profiled."""
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        seen.append(scope["type"])

    await ProfilingMiddleware(inner)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]
