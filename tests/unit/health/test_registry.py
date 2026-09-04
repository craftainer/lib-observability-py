"""Unit test: HealthRegistry collects and concurrently runs registered checks."""

import pytest

from app.config import get_settings
from app.health.base import HealthCheckResult
from app.health.checks import MockHealthCheck
from app.health.registry import HealthRegistry, get_health_registry


class _FakeCheck:
    """A HealthCheck stand-in with a fixed outcome."""

    def __init__(self, name: str, *, healthy: bool) -> None:
        """Bind this check to a name and the fixed result it will report."""
        self.name = name
        self._healthy = healthy

    async def check(self) -> HealthCheckResult:
        """Return the fixed result this fake was constructed with."""
        return HealthCheckResult(self.name, healthy=self._healthy)


async def test_run_all_with_no_registered_checks() -> None:
    """run_all() on an empty registry returns an empty list."""
    assert await HealthRegistry().run_all() == []


async def test_run_all_returns_every_registered_check_result() -> None:
    """run_all() runs every registered check and returns their results in order."""
    registry = HealthRegistry()
    registry.register(_FakeCheck("a", healthy=True))
    registry.register(_FakeCheck("b", healthy=False))

    results = await registry.run_all()

    assert results == [
        HealthCheckResult("a", healthy=True),
        HealthCheckResult("b", healthy=False),
    ]


def test_get_health_registry_registers_mock_checks_in_mock_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_health_registry() registers four MockHealthChecks when MODE=mock."""
    get_health_registry.cache_clear()
    monkeypatch.setattr(get_settings(), "mode", "mock")
    try:
        registry = get_health_registry()
        assert all(isinstance(check, MockHealthCheck) for check in registry._checks)
        assert {check.name for check in registry._checks} == {
            "database",
            "redis",
            "s3",
            "oidc",
        }
    finally:
        get_health_registry.cache_clear()
