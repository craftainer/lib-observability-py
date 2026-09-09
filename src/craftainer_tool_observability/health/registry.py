"""Generic registry of health checks, run together to answer readiness."""

import asyncio

from health.base import HealthCheck, HealthCheckResult


class HealthRegistry:
    """Collects HealthCheck instances and runs them all concurrently."""

    def __init__(self) -> None:
        """Start with an empty set of registered checks."""
        self._checks: list[HealthCheck] = []

    def register(self, check: HealthCheck) -> None:
        """Add a check to be run on the next `run_all`."""
        self._checks.append(check)

    async def run_all(self) -> list[HealthCheckResult]:
        """Run every registered check concurrently and return their results."""
        return list(await asyncio.gather(*(check.check() for check in self._checks)))
