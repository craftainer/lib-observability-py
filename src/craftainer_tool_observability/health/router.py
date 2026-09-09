"""Generic HTTP routes for k8s-style liveness and readiness probes.

Resource-agnostic: takes the caller's own HealthRegistry provider (with whatever concrete
checks it registered) rather than assuming any particular set of external services.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from health.registry import HealthRegistry


def build_health_router(get_registry: Callable[..., HealthRegistry]) -> APIRouter:
    """Build a router exposing /live and /ready against the given HealthRegistry provider."""
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

    return router
