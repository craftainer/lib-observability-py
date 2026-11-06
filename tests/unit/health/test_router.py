"""Unit test: build_health_router's /live and /ready, against a fake HealthRegistry provider.

Fully generic -- no app.main, no concrete check, nothing beyond
craftainer_observability.health itself and FastAPI.
"""

import asyncio
from importlib import metadata

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from craftainer_observability.health import router as router_mod
from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.registry import HealthRegistry
from craftainer_observability.health.router import build_health_router


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


class _CountingCheck:
    """A check that counts runs and can be held open."""

    name = "counting"

    def __init__(self) -> None:
        """Start at zero runs, not held."""
        self.runs = 0
        self.gate: asyncio.Event | None = None

    async def check(self) -> HealthCheckResult:
        """Count, optionally wait for the gate, report healthy."""
        self.runs += 1
        if self.gate is not None:
            await self.gate.wait()
        return HealthCheckResult(self.name, healthy=True)


def _ready_app(
    check: _CountingCheck, ready_cache_ttl: float = 1.0
) -> tuple[FastAPI, HealthRegistry]:
    registry = HealthRegistry()
    registry.register(check)
    app = FastAPI()
    app.include_router(build_health_router(lambda: registry, ready_cache_ttl=ready_cache_ttl))
    return app, registry


async def _get(app: FastAPI, path: str = "/ready") -> int:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return (await c.get(path)).status_code


async def test_ready_result_is_cached_for_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated /ready calls inside the TTL run the checks once; after it, again."""
    now = [100.0]
    monkeypatch.setattr(router_mod, "monotonic", lambda: now[0])
    check = _CountingCheck()
    app, _ = _ready_app(check, ready_cache_ttl=2.0)
    assert [await _get(app) for _ in range(3)] == [200, 200, 200]
    assert check.runs == 1
    now[0] += 2.5
    assert await _get(app) == 200
    assert check.runs == 2


async def test_concurrent_ready_calls_share_one_run() -> None:
    """A flood of simultaneous /ready requests runs each check once."""
    check = _CountingCheck()
    check.gate = asyncio.Event()
    app, _ = _ready_app(check)
    tasks = [asyncio.create_task(_get(app)) for _ in range(10)]
    await asyncio.sleep(0.05)
    check.gate.set()
    assert await asyncio.gather(*tasks) == [200] * 10
    assert check.runs == 1


async def test_ready_ttl_zero_disables_the_cache() -> None:
    """ready_cache_ttl=0 runs the checks on every request."""
    check = _CountingCheck()
    app, _ = _ready_app(check, ready_cache_ttl=0)
    await _get(app)
    await _get(app)
    assert check.runs == 2


async def test_ready_cache_is_per_registry() -> None:
    """A different registry from the dependency is not served the previous one's result."""
    first, second = _CountingCheck(), _CountingCheck()
    registries = [HealthRegistry(), HealthRegistry()]
    registries[0].register(first)
    registries[1].register(second)
    current = [registries[0]]
    app = FastAPI()
    app.include_router(build_health_router(lambda: current[0]))
    await _get(app)
    current[0] = registries[1]
    await _get(app)
    assert (first.runs, second.runs) == (1, 1)
