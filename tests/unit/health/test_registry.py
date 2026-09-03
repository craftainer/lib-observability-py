"""Unit test: HealthRegistry collects and concurrently runs registered checks."""

from app.health.base import HealthCheckResult
from app.health.registry import HealthRegistry


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
