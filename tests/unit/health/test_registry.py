"""Unit test: HealthRegistry collects and concurrently runs registered checks."""

import asyncio
import logging

import pytest

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.registry import (
    DEFAULT_CHECK_TIMEOUT,
    TIMEOUT_DETAIL,
    HealthRegistry,
)


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


class _HangingCheck:
    """A HealthCheck whose check() never finishes on its own."""

    def __init__(self, name: str) -> None:
        """Bind this check to a name."""
        self.name = name

    async def check(self) -> HealthCheckResult:
        """Sleep far longer than any test timeout."""
        await asyncio.sleep(3600)
        return HealthCheckResult(self.name, healthy=True)  # pragma: no cover


def test_default_timeout_is_five_seconds() -> None:
    """The registry-wide default matches HTTPCheck's and lib-observability-rs's."""
    assert DEFAULT_CHECK_TIMEOUT == 5.0


async def test_register_rejects_a_duplicate_name() -> None:
    """A second check under one name raises, and the first stays registered."""
    registry = HealthRegistry()
    registry.register(_FakeCheck("a", healthy=True))
    with pytest.raises(ValueError, match="'a' is already registered"):
        registry.register(_FakeCheck("a", healthy=False))
    assert await registry.run_all() == [HealthCheckResult("a", healthy=True)]


async def test_a_hung_check_is_reported_unhealthy_without_stalling_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A check past the registry timeout is unhealthy with a fixed detail; others still answer."""
    registry = HealthRegistry(default_timeout=0.05)
    registry.register(_HangingCheck("slow"))
    registry.register(_FakeCheck("fast", healthy=True))

    with caplog.at_level(logging.ERROR):
        results = await registry.run_all()

    assert results == [
        HealthCheckResult("slow", healthy=False, detail=TIMEOUT_DETAIL),
        HealthCheckResult("fast", healthy=True),
    ]
    assert "timed out" in caplog.text


async def test_a_per_check_timeout_overrides_the_registry_default() -> None:
    """register(timeout=...) wins over the default, in either direction."""
    registry = HealthRegistry(default_timeout=3600)
    registry.register(_HangingCheck("slow"), timeout=0.05)
    assert await registry.run_all() == [
        HealthCheckResult("slow", healthy=False, detail=TIMEOUT_DETAIL)
    ]
