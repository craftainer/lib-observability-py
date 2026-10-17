"""HTTP health check (the `http` extra), e.g. for an OIDC discovery document.

The failure branch is `# pragma: no cover` for tests/e2e specifically: proving it
requires actually breaking the live shared Keycloak, which isn't a trade worth
making for a smoke-test suite -- the unit tests fake the client to exercise it.
"""

import logging

import httpx

from craftainer_tool_observability.health.base import HealthCheckResult
from craftainer_tool_observability.health.checks._common import UNHEALTHY_DETAIL

logger = logging.getLogger(__name__)


class HTTPCheck:
    """Checks that a URL answers a GET with a success status."""

    def __init__(self, url: str, name: str = "http", timeout: float = 5) -> None:
        """Bind this check to the full URL to GET, reporting under `name`."""
        self.name = name
        self._url = url
        self._timeout = timeout

    async def check(self) -> HealthCheckResult:
        """GET the URL and report whether it succeeded."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url)
                response.raise_for_status()
        except httpx.HTTPError:  # pragma: no cover -- see module docstring
            logger.exception("HTTP health check failed")
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
