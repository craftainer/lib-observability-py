"""Generic HTTP routes for k8s-style liveness and readiness probes.

Resource-agnostic: takes the caller's own HealthRegistry provider (with whatever concrete
checks it registered) rather than assuming any particular set of external services.
"""

import asyncio
import os
import weakref
from collections.abc import Callable
from importlib import metadata
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.registry import HealthRegistry

DEFAULT_READY_CACHE_TTL = 1.0
"""Seconds `/ready` reuses its last result."""


def _package_version(package: str | None) -> str | None:
    """`APP_VERSION` if set, else the installed distribution's version, else None."""
    if version := os.environ.get("APP_VERSION"):
        return version
    if package is None:
        return None
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def build_health_router(
    get_registry: Callable[..., HealthRegistry],
    *,
    include_info: bool = False,
    info_package: str | None = None,
    ready_cache_ttl: float = DEFAULT_READY_CACHE_TTL,
) -> APIRouter:
    """Build a router exposing /live and /ready against the given HealthRegistry provider.

    With `include_info=True` it also exposes `/info` (`{"version", "commit"}` from the
    `APP_VERSION`/`APP_COMMIT` env vars; the version falls back to the metadata of the
    installed distribution `info_package`). Off by default because it discloses the version.

    `/ready` is unauthenticated and every call would hit every dependency, so its result is
    cached for `ready_cache_ttl` seconds (`0` turns the cache off) and concurrent requests
    share one run of the checks. The cache belongs to the router, so `get_registry` is
    expected to return the application's one registry. Mount the router on an internal port (or a path the edge
    does not route) rather than the public one.
    """
    router = APIRouter(tags=["health"])
    # One lock per event loop: an `asyncio.Lock` binds to the loop that first contends it,
    # and a router can be served from more than one (several test clients, worker threads).
    locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
        weakref.WeakKeyDictionary()
    )
    cached: list[tuple[float, list[HealthCheckResult]]] = []

    async def run_checks(registry: HealthRegistry) -> list[HealthCheckResult]:
        """The checks' results, from the cache while fresh; one run serves concurrent callers."""
        if ready_cache_ttl <= 0:
            return await registry.run_all()
        lock = locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            if cached:
                at, results = cached[0]
                if monotonic() - at < ready_cache_ttl:
                    return results
            results = await registry.run_all()
            cached[:] = [(monotonic(), results)]
            return results

    @router.get("/live")
    async def live() -> dict[str, str]:
        """Liveness probe: the process is up and serving requests. Never checks dependencies."""
        return {"status": "ok"}

    @router.get("/ready")
    async def ready(
        response: Response,
        registry: Annotated[HealthRegistry, Depends(get_registry)],
    ) -> dict[str, object]:
        """Readiness probe: every registered external service must be reachable."""
        results = await run_checks(registry)
        healthy = all(result.healthy for result in results)
        response.status_code = (
            status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return {
            "status": "ok" if healthy else "degraded",
            "checks": {
                result.name: {"healthy": result.healthy, "detail": result.detail}
                for result in results
            },
        }

    if include_info:

        @router.get("/info")
        async def info() -> dict[str, str | None]:
            """App version and commit; null for a value that is not known."""
            return {
                "version": _package_version(info_package),
                "commit": os.environ.get("APP_COMMIT") or None,
            }

    return router
