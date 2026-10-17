"""Postgres health check (the `sqlalchemy` extra).

The failure branch is `# pragma: no cover` for tests/e2e specifically: proving it
requires actually breaking the live shared Postgres, which isn't a trade worth
making for a smoke-test suite -- the unit tests fake the engine to exercise it.
"""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from craftainer_tool_observability.health.base import HealthCheckResult
from craftainer_tool_observability.health.checks._common import UNHEALTHY_DETAIL

logger = logging.getLogger(__name__)


class PostgresCheck:
    """Checks that Postgres answers a trivial query through the given async engine."""

    def __init__(self, engine: AsyncEngine, name: str = "postgres") -> None:
        """Bind this check to an async engine, reporting under `name`."""
        self.name = name
        self._engine = engine

    async def check(self) -> HealthCheckResult:
        """Run `SELECT 1` and report whether it succeeded."""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except SQLAlchemyError:  # pragma: no cover -- see module docstring
            logger.exception("Database health check failed")
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
