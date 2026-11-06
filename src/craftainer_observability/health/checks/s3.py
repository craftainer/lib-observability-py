"""S3 health check (the `s3` extra).

The unit tests fake the client to exercise the failure branch.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from craftainer_observability.health.base import UNHEALTHY_DETAIL, HealthCheckResult
from craftainer_observability.health.redact import log_failure

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
    """Checks that the S3-compatible endpoint answers a ListBuckets call.

    The call runs on this check's own single-thread executor, not the event loop's default
    one (which also serves DNS lookups). A call that outlives the registry's timeout keeps
    its thread, so while it runs later checks wait for that same call instead of queueing
    another: a slow S3 plus a flood of `/ready` requests ties up one thread, never more.
    """

    def __init__(self, client: S3Client, name: str = "s3") -> None:
        """Bind this check to a boto3 S3 client, reporting under `name`."""
        self.name = name
        self._client = client
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="s3-health")
        self._inflight: asyncio.Future[object] | None = None

    def close(self) -> None:
        """Release the executor thread (call on shutdown; a running call is not interrupted)."""
        self._executor.shutdown(wait=False)

    def _call(self) -> asyncio.Future[object]:
        """The running `ListBuckets` call on this loop, started when there is none."""
        loop = asyncio.get_running_loop()
        inflight = self._inflight
        if inflight is None or inflight.done() or inflight.get_loop() is not loop:
            inflight = loop.run_in_executor(self._executor, self._client.list_buckets)
            # Retrieve the outcome even if every waiter timed out, so it is never "unretrieved".
            inflight.add_done_callback(lambda fut: fut.cancelled() or fut.exception())
            self._inflight = inflight
        return inflight

    async def check(self) -> HealthCheckResult:
        """Call ListBuckets in a thread and report whether it succeeded."""
        try:
            await asyncio.shield(self._call())
        except (BotoCoreError, ClientError) as exc:
            log_failure(logger, "S3 health check failed", exc)
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
