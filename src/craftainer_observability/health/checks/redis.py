"""Redis health check (the `redis` extra).

The unit tests fake the client to exercise the failure branch.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.checks._common import UNHEALTHY_DETAIL, log_failure

logger = logging.getLogger(__name__)


class RedisCheck:
    """Checks that Redis answers PING, over one client reused by every check.

    The client (and its connection pool) is created on the first check and kept, so a
    probe every few seconds does not open a connection each time. It belongs to the event
    loop that ran the first check: use one `RedisCheck` per loop, and call `aclose()` on
    shutdown.
    """

    def __init__(self, url: str, name: str = "redis") -> None:
        """Bind this check to a Redis connection URL, reporting under `name`."""
        self.name = name
        self._url = url
        self._client: Redis | None = None

    async def check(self) -> HealthCheckResult:
        """PING Redis and report whether it succeeded."""
        if self._client is None:
            self._client = Redis.from_url(self._url)
        try:
            await self._client.ping()
        except RedisError as exc:
            log_failure(logger, "Redis health check failed", exc)
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)

    async def aclose(self) -> None:
        """Close the reused client; a later check opens a fresh one."""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
