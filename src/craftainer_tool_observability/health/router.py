"""Generic HTTP routes for k8s-style liveness and readiness probes.

Resource-agnostic: takes the caller's own HealthRegistry provider (with whatever concrete
checks it registered) rather than assuming any particular set of external services.
"""

import os
from collections.abc import Callable
from importlib import metadata
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from craftainer_tool_observability.health.registry import HealthRegistry


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
) -> APIRouter:
    """Build a router exposing /live and /ready against the given HealthRegistry provider.

    With `include_info=True` it also exposes `/info` (`{"version", "commit"}` from the
    `APP_VERSION`/`APP_COMMIT` env vars; the version falls back to the metadata of the
    installed distribution `info_package`). Off by default because it discloses the version.
    """
    router = APIRouter(tags=["health"])

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
        results = await registry.run_all()
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
