"""Shared by every check in this package."""

import logging
import traceback

from craftainer_observability.health.redact import redact

# GET /ready is unauthenticated (a k8s-style readiness probe) -- the real
# exception text (which can embed a DSN host/port/credentials, an S3 endpoint,
# or an OIDC issuer's response body) goes only to the logs, for whoever's on
# call to look up; the HTTP response gets this fixed string regardless of which
# service failed or why.
UNHEALTHY_DETAIL = "dependency check failed; see server logs"


def log_failure(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Log `message` and `exc` with its traceback, credentials redacted.

    Unlike `logger.exception`, the traceback text (which carries the exception message,
    and so any DSN in it) goes through `redact` before it is logged.
    """
    trace = "".join(traceback.format_exception(exc))
    logger.error("%s\n%s", message, redact(trace))
