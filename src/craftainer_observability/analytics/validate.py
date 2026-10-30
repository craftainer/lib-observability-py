"""Validation and sanitising of event names, properties and URLs (contract I3)."""

import logging
import math
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_NAME = 50
MAX_VALUE = 200
MAX_URL = 500

Scalar = str | int | float | bool


def valid_name(name: object) -> bool:
    """Whether `name` is a well-formed event name of at most 50 characters."""
    return isinstance(name, str) and len(name) <= MAX_NAME and NAME_RE.fullmatch(name) is not None


def clean_props(props: object, allowed: frozenset[str]) -> dict[str, Scalar]:
    """Keep allowlisted scalar properties, truncating strings; drop (and log) the rest.

    Only the key is ever logged, never the value.
    """
    if not props:
        return {}
    if not isinstance(props, Mapping):
        logger.debug("analytics props dropped: not a mapping")
        return {}
    out: dict[str, Scalar] = {}
    for key, value in props.items():
        if not isinstance(key, str) or key not in allowed:
            logger.debug("analytics prop dropped: key not on the allowlist")
        elif not isinstance(value, str | int | float | bool) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            logger.debug("analytics prop %r dropped: not a scalar", key)
        elif isinstance(value, str):
            out[key] = value[:MAX_VALUE]
        else:
            out[key] = value
    return out


def clean_url(url: str | None) -> str:
    """The path of `url` with query and fragment stripped; `/` when empty or not a string."""
    if not isinstance(url, str) or not url:
        return "/"
    path = urlsplit(url).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path[:MAX_URL]
