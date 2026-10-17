"""Unit test: build_health_router's /live and /ready, against a fake HealthRegistry provider.

Fully generic -- no app.main, no concrete check, nothing beyond
craftainer_tool_observability.health itself and FastAPI.
"""

from importlib import metadata

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from craftainer_tool_observability.health.base import HealthCheckResult
from craftainer_tool_observability.health.registry import HealthRegistry
from craftainer_tool_observability.health.router import build_health_router


class _FakeCheck:
    """A HealthCheck stand-in with a fixed outcome."""

    def __init__(self, name: str, *, healthy: bool) -> None:
        """Bind this check to a name and the fixed result it will report."""
        self.name = name
        self._healthy = healthy

    async def check(self) -> HealthCheckResult:
        """Return the fixed result this fake was constructed with."""
        return HealthCheckResult(self.name, healthy=self._healthy)


def _client(*, healthy: bool) -> TestClient:
    """Build a standalone app with one fake check registered, healthy or not."""
    registry = HealthRegistry()
    registry.register(_FakeCheck("fake", healthy=healthy))
    app = FastAPI()
    app.include_router(build_health_router(lambda: registry))
    return TestClient(app)


def test_live_returns_ok_without_running_any_check() -> None:
    """GET /live returns 200 and the static ok payload, no dependency checks."""
    response = _client(healthy=False).get("/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_every_check_passes() -> None:
    """GET /ready returns 200 when every registered check is healthy."""
    response = _client(healthy=True).get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["fake"]["healthy"] is True


def test_ready_when_a_check_fails() -> None:
    """GET /ready returns 503 when a registered check is unhealthy."""
    response = _client(healthy=False).get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def _info_client(*, include_info: bool, info_package: str | None = None) -> TestClient:
    """Build a standalone app whose router has /info on or off."""
    app = FastAPI()
    app.include_router(
        build_health_router(HealthRegistry, include_info=include_info, info_package=info_package)
    )
    return TestClient(app)


def test_info_is_absent_by_default() -> None:
    """GET /info is a 404 unless the router was built with include_info=True."""
    assert _info_client(include_info=False).get("/info").status_code == 404


def test_info_reports_env_version_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_VERSION and APP_COMMIT are reported verbatim."""
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    monkeypatch.setenv("APP_COMMIT", "abc123")
    response = _info_client(include_info=True).get("/info")
    assert response.json() == {"version": "1.2.3", "commit": "abc123"}


def test_info_falls_back_to_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without APP_VERSION the installed distribution's version is used; no commit is null."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("APP_COMMIT", raising=False)
    response = _info_client(include_info=True, info_package="pytest").get("/info")
    assert response.json() == {"version": metadata.version("pytest"), "commit": None}


@pytest.mark.parametrize("package", [None, "no-such-distribution-xyz"])
def test_info_version_is_null_when_unknown(
    monkeypatch: pytest.MonkeyPatch, package: str | None
) -> None:
    """No env var and no (or an unknown) package gives a null version."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    response = _info_client(include_info=True, info_package=package).get("/info")
    assert response.json()["version"] is None
