"""Generic registry of health checks, run together to answer readiness."""

import asyncio
import logging

from craftainer_observability.health.base import HealthCheck, HealthCheckResult

logger = logging.getLogger(__name__)

DEFAULT_CHECK_TIMEOUT = 5.0
"""Seconds a check gets when neither it nor the registry sets another limit."""

TIMEOUT_DETAIL = "health check timed out"
"""The fixed, client-safe note for a check that exceeded its timeout."""


class HealthRegistry:
    """Collects HealthCheck instances and runs them all concurrently, each under a timeout."""

    def __init__(self, default_timeout: float = DEFAULT_CHECK_TIMEOUT) -> None:
        """Start with no checks; `default_timeout` (seconds) applies to every check."""
        self._checks: list[tuple[HealthCheck, float | None]] = []
        self._names: set[str] = set()
        self._default_timeout = default_timeout

    def register(self, check: HealthCheck, *, timeout: float | None = None) -> None:
        """Add a check to be run on the next `run_all`.

        `timeout` (seconds) overrides the registry default for this check. Raises
        `ValueError` if a check with the same name is already registered: `/ready`
        keys its response by name, so a duplicate would hide the first one's result.
        """
        if check.name in self._names:
            msg = f"a health check named {check.name!r} is already registered"
            raise ValueError(msg)
        self._names.add(check.name)
        self._checks.append((check, timeout))

    async def _run_one(self, check: HealthCheck, timeout: float | None) -> HealthCheckResult:
        """Run one check; one that exceeds its timeout is reported unhealthy."""
        limit = self._default_timeout if timeout is None else timeout
        try:
            async with asyncio.timeout(limit):
                return await check.check()
        except TimeoutError:
            logger.error(
                "Health check timed out",
                extra={"check": check.name, "timeout_seconds": limit},
            )
            return HealthCheckResult(check.name, healthy=False, detail=TIMEOUT_DETAIL)

    async def run_all(self) -> list[HealthCheckResult]:
        """Run every registered check concurrently and return their results in order."""
        return list(
            await asyncio.gather(*(self._run_one(check, limit) for check, limit in self._checks))
        )
