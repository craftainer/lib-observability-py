"""Health check interface: the contract every registered external-service check implements."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HealthCheckResult:
    """Outcome of running one registered health check."""

    name: str
    healthy: bool
    detail: str | None = None


class HealthCheck(Protocol):
    """A named, async check against one external service."""

    name: str

    async def check(self) -> HealthCheckResult:
        """Run the check and return its result."""
        ...
