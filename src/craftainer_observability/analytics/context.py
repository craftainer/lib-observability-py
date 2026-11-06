"""Per-request analytics context: GPC/DNT and consent, framework-agnostic.

A web framework's middleware wraps each request in `request_context(...)`, passing what
it read from the headers (`from_headers(request.headers)` does the parsing: `Sec-GPC: 1`,
`DNT: 1`) and, for `ANALYTICS_CONSENT=opt-in`, whether the visitor consented. `track()`
inside that scope consults it. The state is a `contextvars.ContextVar`, so it follows
`asyncio` tasks and threads started with a copied context, and never leaks between concurrent
requests.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """What the current request says about tracking."""

    gpc: bool = False
    """`Sec-GPC: 1` or `DNT: 1` was sent."""
    consent: bool = False
    """The visitor opted in (only consulted when `ANALYTICS_CONSENT=opt-in`)."""


_current: ContextVar[RequestContext | None] = ContextVar("analytics_request_context", default=None)


def _header(headers: Mapping[str, str], name: str) -> str:
    """The value of `name` (case-insensitive), stripped; empty when absent."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value.strip()
    return ""


def from_headers(headers: Mapping[str, str]) -> RequestContext:
    """The `RequestContext` for a request's headers (names are case-insensitive).

    `gpc` is true for `Sec-GPC: 1` or `DNT: 1`; `consent` is true for `Analytics-Consent: 1`.
    Any other value, or an absent header, is false. Only the BFF (the edge that sees the
    visitor's choice) may set `Analytics-Consent`: strip it from untrusted traffic, because
    a service reachable without the BFF would otherwise accept a forged consent.
    """
    return RequestContext(
        gpc=_header(headers, "Sec-GPC") == "1" or _header(headers, "DNT") == "1",
        consent=_header(headers, "Analytics-Consent") == "1",
    )


def get_request_context() -> RequestContext | None:
    """The active context, or `None` outside `request_context`."""
    return _current.get()


@contextmanager
def request_context(*, gpc: bool = False, consent: bool = False) -> Iterator[RequestContext]:
    """Mark the enclosed code as one request with these GPC/DNT and consent signals."""
    ctx = RequestContext(gpc=gpc, consent=consent)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
