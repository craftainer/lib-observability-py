"""Credential redaction for the error text a failing check logs.

A dependency's error often embeds its connection string
(`postgres://user:pass@host/db`, `password=...`), and the real error goes to the logs
even though `/ready` shows a fixed string. `redact` strips the credentials from that
text before it is logged. A safety net, not a parser: it over-redacts rather than leak.
Same patterns as `lib-observability-rs`.
"""

import logging
import re
import traceback

REDACTED = "[REDACTED]"

# `scheme://user:pass@host` -> `scheme://[REDACTED]@host`. The userinfo runs to the last
# `@` before whitespace or a quote (not to the first `/`: a password may contain `/`, `?`
# or `#`), so a path containing `@` is over-redacted on purpose.
_USERINFO = re.compile(r"""(://)[^\s"']*@""")

# `password=hunter2`, `token: 'a b'`. Keys match case-insensitively, also as the tail of a
# longer key (`db_password`). Longer keys come first so `secret_key` wins over `secret`.
# The value is a quoted string (backslash escapes honoured; unterminated runs to the end)
# or a run up to whitespace or a quote.
_PAIR = re.compile(
    r"""(?P<key>secret_key|secret|password|passwd|pwd|token|api_key|apikey|access_key|private_key|authorization)"""
    r"""(?P<sep>["']? *[=:] *)"""
    r"""(?:(?:bearer|basic) +)?"""
    r"""(?:'(?:\\.|[^'\\])*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)|[^\s'"]*)""",
    re.IGNORECASE | re.DOTALL,
)


def redact(text: str) -> str:
    """`text` with URL userinfo and secret key/value pairs replaced by `[REDACTED]`."""
    text = _USERINFO.sub(rf"\1{REDACTED}@", text)
    return _PAIR.sub(rf"\g<key>\g<sep>{REDACTED}", text)


def log_failure(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Log `message` and `exc` with its traceback, credentials redacted.

    Unlike `logger.exception` (whose `exc_info` would carry the raw text), the type goes in
    `exception_type` and the traceback text (which carries the exception message,
    and so any DSN in it) goes through `redact` before it is logged.
    """
    trace = "".join(traceback.format_exception(exc))
    logger.error("%s\n%s", message, redact(trace), extra={"exception_type": type(exc).__qualname__})
