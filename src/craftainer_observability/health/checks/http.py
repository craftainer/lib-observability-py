"""HTTP health check (the `http` extra), e.g. for an OIDC discovery document.

The unit tests fake the client to exercise the failure branch.
"""

import logging

import httpx

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.checks._common import UNHEALTHY_DETAIL, log_failure

logger = logging.getLogger(__name__)


class HTTPCheck:
    """Checks that a URL answers a GET with a success status."""

    def __init__(self, url: str, name: str = "http", timeout: float = 5) -> None:
        """Bind this check to the full URL to GET, reporting under `name`."""
        self.name = name
        self._url = url
        self.timeout = timeout

    async def check(self) -> HealthCheckResult:
        """GET the URL and report whether it succeeded."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self._url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            log_failure(logger, "HTTP health check failed", exc)
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
