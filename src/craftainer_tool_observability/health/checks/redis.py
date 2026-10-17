"""Redis health check (the `redis` extra).

The failure branch is `# pragma: no cover` for tests/e2e specifically: proving it
requires actually breaking the live shared Redis, which isn't a trade worth
making for a smoke-test suite -- the unit tests fake the client to exercise it.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from craftainer_tool_observability.health.base import HealthCheckResult
from craftainer_tool_observability.health.checks._common import UNHEALTHY_DETAIL

logger = logging.getLogger(__name__)


class RedisCheck:
    """Checks that Redis answers PING."""

    def __init__(self, url: str, name: str = "redis") -> None:
        """Bind this check to a Redis connection URL, reporting under `name`."""
        self.name = name
        self._url = url

    async def check(self) -> HealthCheckResult:
        """PING Redis and report whether it succeeded."""
        client = Redis.from_url(self._url)
        try:
            await client.ping()
        except RedisError:  # pragma: no cover -- see module docstring
            logger.exception("Redis health check failed")
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        finally:
            await client.aclose()
        return HealthCheckResult(self.name, healthy=True)
