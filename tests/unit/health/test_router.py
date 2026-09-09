"""Unit test: build_health_router's /live and /ready, against a fake HealthRegistry provider.

Fully generic -- no app.main, no concrete check, nothing beyond health/ itself and FastAPI.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from health.base import HealthCheckResult
from health.registry import HealthRegistry
from health.router import build_health_router


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
