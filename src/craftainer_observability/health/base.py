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


# GET /ready is unauthenticated (a k8s-style readiness probe) -- the real
# exception text (which can embed a DSN host/port/credentials, an S3 endpoint,
# or an OIDC issuer's response body) goes only to the logs, for whoever's on
# call to look up; the HTTP response gets this fixed string regardless of which
# service failed or why.
UNHEALTHY_DETAIL = "dependency check failed; see server logs"
