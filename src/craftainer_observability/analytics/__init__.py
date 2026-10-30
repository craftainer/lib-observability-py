"""Vendor-neutral product analytics: `track()` with a privacy-first default (`[analytics]`)."""

from craftainer_observability.analytics.client import AnalyticsClient, shutdown, track
from craftainer_observability.analytics.config import AnalyticsConfig
from craftainer_observability.analytics.context import (
    RequestContext,
    get_request_context,
    request_context,
)

__all__ = [
    "AnalyticsClient",
    "AnalyticsConfig",
    "RequestContext",
    "get_request_context",
    "request_context",
    "shutdown",
    "track",
]
