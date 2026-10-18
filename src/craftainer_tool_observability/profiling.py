"""Opt-in on-demand request profiling with pyinstrument.

`install_profiling(app, enabled=..., production=...)` does nothing when disabled and
raises when enabled in production (the profiler adds overhead and exposes internals). When
enabled, a request carrying `?profile=1` (the parameter name is configurable) is
executed under pyinstrument and answered with the profile as HTML instead of its normal
response; every other request is untouched. pyinstrument is a dev/`[profiling]` dependency,
so it is imported lazily and only when enabled. This module imports nothing from `app`;
the caller passes the deployment mode in as `production`.
"""

from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_QUERY_PARAM = "profile"


class ProfilingDisabledError(RuntimeError):
    """Raised when profiling is requested in a production deployment."""


class ProfilingMiddleware:
    """Pure ASGI middleware that returns a pyinstrument HTML report on `?<param>=1`."""

    def __init__(self, app: ASGIApp, query_param: str = DEFAULT_QUERY_PARAM) -> None:
        """Wrap `app`; `query_param` is the query parameter that triggers profiling."""
        self.app = app
        self.query_param = query_param

    def _wants_profile(self, scope: Scope) -> bool:
        """Whether this request's query string asks for a profile."""
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        return "1" in query.get(self.query_param, [])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Profile the wrapped app for a flagged HTTP request; pass everything else through."""
        if scope["type"] != "http" or not self._wants_profile(scope):
            await self.app(scope, receive, send)
            return

        from pyinstrument import Profiler  # noqa: PLC0415 # lazy: dev/extra dependency

        async def discard(_message: Message) -> None:
            return None

        profiler = Profiler(async_mode="enabled")
        profiler.start()
        try:
            await self.app(scope, receive, discard)
        finally:
            profiler.stop()
        body = profiler.output_html().encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def install_profiling(
    app: Starlette,
    *,
    enabled: bool,
    production: bool,
    query_param: str = DEFAULT_QUERY_PARAM,
) -> None:
    """Add the profiling middleware to `app` when `enabled`.

    Does nothing when `enabled` is false. Raises `ProfilingDisabledError` when `enabled` and
    `production` are both true. Call it before the app starts serving.
    """
    if not enabled:
        return
    # e2e-unreachable: a production process can't boot under the e2e env; covered by
    # tests/unit/craftainer_tool_observability/test_profiling.py
    if production:  # pragma: no cover
        msg = "request profiling must not be enabled in production"
        raise ProfilingDisabledError(msg)
    import pyinstrument  # noqa: F401, PLC0415 # fail early and clearly if the extra is missing

    app.add_middleware(ProfilingMiddleware, query_param=query_param)
