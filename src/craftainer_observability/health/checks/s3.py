"""S3 health check (the `s3` extra).

The unit tests fake the client to exercise the failure branch.
"""

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
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
        self._inflight: Future[object] | None = None
        self._closed = False

    def close(self) -> None:
        """Release the executor; idempotent. Later checks report unhealthy.

        A call already running is not interrupted (its thread is joined at interpreter
        exit); queued work is dropped.
        """
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _call(self) -> Future[object]:
        """The running `ListBuckets` call, started when there is none.

        A `concurrent.futures.Future`, so it does not belong to any one event loop: each
        caller wraps it for its own loop, and a loop that has gone away cannot break it.
        """
        inflight = self._inflight
        if inflight is None or inflight.done():
            inflight = self._executor.submit(self._client.list_buckets)
            # Retrieve the outcome even if every waiter timed out, so it is never "unretrieved".
            inflight.add_done_callback(lambda fut: fut.cancelled() or fut.exception())
            self._inflight = inflight
        return inflight

    async def check(self) -> HealthCheckResult:
        """Call ListBuckets in a thread and report whether it succeeded."""
        if self._closed:
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        try:
            wrapped = asyncio.wrap_future(self._call())
            # Retrieve the outcome even if this waiter is cancelled, so it is never logged
            # as "exception in shielded future".
            wrapped.add_done_callback(lambda fut: fut.cancelled() or fut.exception())
            await asyncio.shield(wrapped)
        except (BotoCoreError, ClientError) as exc:
            log_failure(logger, "S3 health check failed", exc)
            return HealthCheckResult(self.name, healthy=False, detail=UNHEALTHY_DETAIL)
        return HealthCheckResult(self.name, healthy=True)
