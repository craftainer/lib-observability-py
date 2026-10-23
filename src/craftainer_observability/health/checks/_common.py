"""Shared by every check in this package."""

from craftainer_observability.health.base import UNHEALTHY_DETAIL
from craftainer_observability.health.redact import log_failure

__all__ = ["UNHEALTHY_DETAIL", "log_failure"]
