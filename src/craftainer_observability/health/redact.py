"""Credential redaction for the error text a failing check logs.

A dependency's error often embeds its connection string
(`postgres://user:pass@host/db`, `password=...`), and the real error goes to the logs
even though `/ready` shows a fixed string. `redact` strips the credentials from that
text before it is logged. A safety net, not a parser: it over-redacts rather than leak.
The key list is shared with the logging and OTLP sinks (`craftainer_observability.redaction`).
"""

import logging
import traceback

from craftainer_observability.redaction import REDACTED, redact_text

__all__ = ["REDACTED", "log_failure", "redact"]

redact = redact_text
"""Alias kept for the checks: URL userinfo and secret pairs replaced by `[REDACTED]`."""


def log_failure(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Log `message` and `exc` with its traceback, credentials redacted.

    Unlike `logger.exception` (whose `exc_info` would carry the raw text), the type goes in
    `exception_type` and the traceback text (which carries the exception message,
    and so any DSN in it) goes through `redact` before it is logged.
    """
    trace = "".join(traceback.format_exception(exc))
    logger.error("%s\n%s", message, redact(trace), extra={"exception_type": type(exc).__qualname__})
