"""One shared credential-redaction rule set for every sink (logs, OTLP logs, spans, health).

`REDACTION_KEYS` is the single key list. A key is sensitive when, after lowercasing and
dropping `-` and `_`, it contains any entry (`X-Api-Key`, `db_password` and `Set-Cookie`
all match). `lib-observability-rs` mirrors this list and the text patterns below, so a
change here is a change there. Not a parser: it over-redacts rather than leak.
"""

import re
from collections.abc import Iterable, Mapping

REDACTED = "[REDACTED]"

REDACTION_KEYS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "authorization",
    "claims",
    "credential",
    "apikey",
    "accesskey",
    "privatekey",
    "cookie",
    "bearer",
    "jwt",
    "session",
    "dsn",
    "signature",
)
"""Canonical form: lowercase, no `-` or `_`. Matched as a substring of the normalised key."""

_MAX_DEPTH = 4


def is_sensitive_key(key: str) -> bool:
    """Whether a log field, header or attribute named `key` should have its value redacted."""
    normalised = key.lower().replace("-", "").replace("_", "")
    return any(entry in normalised for entry in REDACTION_KEYS)


def _key_pattern(entry: str) -> str:
    """`apikey` -> a pattern that also accepts `api_key` and `api-key`."""
    return "[-_]?".join(re.escape(char) for char in entry)


# `scheme://user:pass@host` -> `scheme://[REDACTED]@host`. The userinfo runs to the last
# `@` before whitespace or a quote (not to the first `/`: a password may contain `/`, `?`
# or `#`), so a path containing `@` is over-redacted on purpose.
_USERINFO = re.compile(r"""(://)[^\s"']*@""")

# `password=hunter2`, `token: 'a b'`. Keys match case-insensitively, also as the tail of a
# longer key (`db_password`, `secret_key`, `session_id`). The value is a quoted string
# (backslash escapes honoured; unterminated runs to the end) or a run up to whitespace or
# a quote.
_PAIR = re.compile(
    r"(?P<key>(?:" + "|".join(_key_pattern(entry) for entry in REDACTION_KEYS) + r")[\w-]*)"
    r"""(?P<sep>["']? *[=:] *)"""
    r"""(?:(?:bearer|basic) +)?"""
    r"""(?:'(?:\\.|[^'\\])*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)|[^\s'"]*)""",
    re.IGNORECASE | re.DOTALL,
)

# A bare `Bearer <credential>` and a bare JWT (three base64url parts starting `eyJ`) that
# no `key=` precedes, e.g. `logger.info("login %s", token)`.
_BEARER = re.compile(r"\b(bearer) +[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def redact_text(text: str) -> str:
    """`text` with URL userinfo, secret key/value pairs, bearer credentials and JWTs hidden."""
    text = _USERINFO.sub(rf"\1{REDACTED}@", text)
    text = _PAIR.sub(rf"\g<key>\g<sep>{REDACTED}", text)
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    return _JWT.sub(REDACTED, text)


def redact_value(key: str, value: object, _depth: int = 0) -> object:
    """`value` of the field `key`: replaced when the key is sensitive, else scrubbed.

    Strings go through `redact_text`; mappings and sequences are walked (a nested level
    deeper than four is replaced outright); any other non-primitive object is replaced
    by its redacted `str()`, so a `repr` cannot carry a credential past the filter.
    """
    if is_sensitive_key(key):
        return REDACTED
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return _scrub_container(value, _depth)
    return redact_text(value if isinstance(value, str) else str(value))


def _scrub_container(value: Mapping[object, object] | Iterable[object], depth: int) -> object:
    """A mapping or sequence with every nested value scrubbed; too deep means replaced."""
    if depth >= _MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): redact_value(str(k), v, depth + 1) for k, v in value.items()}
    return [redact_value("", item, depth + 1) for item in value]
