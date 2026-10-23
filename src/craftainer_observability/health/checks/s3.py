"""S3 health check (the `s3` extra).

The unit tests fake the client to exercise the failure branch.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.checks._common import UNHEALTHY_DETAIL, log_failure

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # boto3-stubs[s3] is a dev-only dependency (pyproject.toml) -- not
    # installed in the runner image, so this can't be a runtime import.
    # Safe as a type-only import: the one use below is a parameter annotation
    # on a function that never evaluates it at runtime (no
    # `from __future__ import annotations` needed on Python 3.14, where
    # annotations are lazy).
    from mypy_boto3_s3 import S3Client


class S3Check:
    """Checks that the S3-compatible endpoint answers a ListBuckets call."""

    def __init__(self, client: S3Client, name: str = "s3") -> None:
        """Bind this check to a boto3 S3 client, reporting under `name`."""
        self.name = name
        self._client = client

    async def check(self) -> HealthCheckResult:
        """Call ListBuckets in a thread and report whether it succeeded."""
        try:
            await asyncio.to_thread(self._client.list_buckets)
        except (BotoCoreError, ClientError) as exc:
            log_failure(logger, "S3 health check failed", exc)
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
