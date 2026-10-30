"""Per-request analytics context: GPC/DNT and consent, framework-agnostic.

A web framework's middleware wraps each request in `request_context(...)`, passing what
it read from the headers (`Sec-GPC: 1`, `DNT: 1`) and, for `ANALYTICS_CONSENT=opt-in`,
whether the visitor consented. `track()` inside that scope consults it. The state is a
`contextvars.ContextVar`, so it follows `asyncio` tasks and threads started with a copied
context, and never leaks between concurrent requests.
"""

from collections.abc import Iterator
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
