"""One shared credential-redaction rule set for every sink (logs, OTLP logs, spans, health).

`REDACTION_KEYS` is the single key list. A key is sensitive when, after lowercasing and
with `-` read as `_`, it contains any entry (`X-Api-Key`, `db_password` and `Set-Cookie`
all match). `lib-observability-rs` mirrors this list and the text patterns below, so a
change here is a change there. Not a parser: it over-redacts rather than leak.
"""

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache

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
    "api_key",
    "apikey",
    "access_key",
    "cookie",
    "bearer",
    "jwt",
    "session",
    "dsn",
    "private",
    "signature",
)
"""Lowercase. A key is sensitive when its lowercased form contains an entry."""

_MAX_DEPTH = 4


_SENSITIVE_KEY = re.compile("|".join(map(re.escape, REDACTION_KEYS)))


@lru_cache(maxsize=2048)
def is_sensitive_key(key: str) -> bool:
    """Whether a log field, header or attribute named `key` should have its value redacted.

    Cached: field names come from code and a small set of headers, and the answer is pure.
    """
    return _SENSITIVE_KEY.search(key.lower().replace("-", "_")) is not None


def _key_pattern(entry: str) -> str:
    """`api_key` -> a pattern that also accepts `api-key` and `apikey`."""
    return "[-_]?".join(re.escape(part) for part in entry.split("_"))


# `scheme://user:pass@host` -> `scheme://[REDACTED]@host`. The userinfo runs to the last
# `@` before whitespace or a quote (not to the first `/`: a password may contain `/`, `?`
# or `#`), so a path containing `@` is over-redacted on purpose. Done per whitespace/quote
# delimited token with `find`, so a long run of `://` costs linear, not quadratic, time.
_TOKEN = re.compile(r"""[^\s"']+""")


def _redact_userinfo(match: re.Match[str]) -> str:
    """One token with the credentials of its first `://...@` hidden."""
    token = match.group()
    start = token.find("://")
    if start < 0:
        return token
    at = token.rfind("@")
    if at < start + 3:
        return token
    return f"{token[: start + 3]}{REDACTED}{token[at:]}"


_KEY_TAIL = r"[\w-]{0,128}"
"""How far past a matched entry a key may run (`password_hash`); bounded for ReDoS."""

# `Authorization: Digest username="bob", response="abc"`: any scheme word, then either
# `name=value` parameters or one credential. A quoted value is taken whole.
_AUTH = re.compile(
    r"(?P<key>(?:proxy[-_]?)?authorization" + _KEY_TAIL + r")"
    r"""(?P<sep>\\?["']? *[=:] *)"""
    r"""(?:(?P<q>["'])(?:\\.|(?!(?P=q))[^\\])*(?:(?P=q)|$)"""
    r"""|(?:[A-Za-z][\w-]*[ ]+)?(?:(?:[\w-]+=(?:"[^"]*"|[^\s,"']*)(?:, *)?)+|[^\s'"]*))""",
    re.IGNORECASE,
)

# `password=hunter2`, `token: 'a b'`. Keys match case-insensitively, also as the tail of a
# longer key (`db_password`, `secret_key`, `session_id`). The value is a quoted string
# (backslash escapes honoured; unterminated runs to the end), the same inside JSON that
# was escaped once more (`\"hunter2\"`), or a run up to whitespace or a quote.
_PAIR = re.compile(
    r"(?P<key>(?:"
    + "|".join(_key_pattern(entry) for entry in REDACTION_KEYS)
    + r")"
    + _KEY_TAIL
    + r")"
    r"""(?P<sep>\\?["']? *[=:] *)"""
    r"""(?:(?:bearer|basic) +)?"""
    r"""(?:'(?:\\.|[^'\\])*(?:'|$)|"(?:\\.|[^"\\])*(?:"|$)"""
    r"""|\\"[^"]*(?:"|$)|\\'[^']*(?:'|$)|[^\s'"]*)""",
    re.IGNORECASE | re.DOTALL,
)

# A bare `Bearer <credential>` and a bare JWT (three base64url parts starting `eyJ`) that
# no `key=` precedes, e.g. `logger.info("login %s", token)`. The JWT parts are bounded and
# possessive, so `eyJ-eyJ-...` cannot make the scan quadratic.
_BEARER = re.compile(r"\b(bearer) +[A-Za-z0-9._~+/=-]{1,4096}", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{1,4096}+\.[A-Za-z0-9_-]{1,4096}+\.[A-Za-z0-9_-]{0,4096}+")


def redact_text(text: str) -> str:
    """`text` with URL userinfo, secret key/value pairs, bearer credentials and JWTs hidden.

    Each rule is skipped by a plain substring test when the text cannot contain its trigger
    (`://`, a `=` or `:`, `bearer`, `eyJ`), so ordinary log lines cost a few `in` checks.
    """
    if "://" in text:
        text = _TOKEN.sub(_redact_userinfo, text)
    if "=" in text or ":" in text:
        if "authorization" in text.lower():
            text = _AUTH.sub(rf"\g<key>\g<sep>{REDACTED}", text)
        text = _PAIR.sub(rf"\g<key>\g<sep>{REDACTED}", text)
    if "bearer" in text.lower():
        text = _BEARER.sub(rf"\1 {REDACTED}", text)
    if "eyJ" in text:
        text = _JWT.sub(REDACTED, text)
    return text


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
