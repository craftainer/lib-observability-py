"""Analytics settings, read from the `ANALYTICS_*` environment variables (contract I3)."""

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

BACKENDS = ("none", "log", "umami", "otel")
CONSENTS = ("implied", "opt-in")
GEOS = ("none", "coarse")


class AnalyticsConfigError(ValueError):
    """An `ANALYTICS_*` variable has a value that cannot be honoured; fix it and restart.

    Unknown configuration fails closed: an unknown `ANALYTICS_ENABLED`, `_BACKEND`, `_CONSENT`,
    `_GEO` or `_RESPECT_GPC`, and an `ANALYTICS_ENDPOINT` that is not a usable http(s) URL.
    """


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _flag(name: str, *, default: bool) -> bool:
    """A boolean variable; an unknown value raises."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    msg = f"{name}={value!r} is not a boolean; use one of {sorted(_TRUE | _FALSE)}"
    raise AnalyticsConfigError(msg)


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    """A choice variable; an unknown value raises."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value not in allowed:
        msg = f"{name}={value!r} is not one of {allowed}"
        raise AnalyticsConfigError(msg)
    return value


def _endpoint() -> str:
    """`ANALYTICS_ENDPOINT`, validated: an http(s) URL with a host and no embedded credentials."""
    endpoint = os.environ.get("ANALYTICS_ENDPOINT", "").strip().rstrip("/")
    if not endpoint:
        return ""
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
    except ValueError as exc:
        msg = "ANALYTICS_ENDPOINT is not a valid URL"
        raise AnalyticsConfigError(msg) from exc
    if parts.scheme not in {"http", "https"} or not host:
        msg = "ANALYTICS_ENDPOINT must be an http(s) URL with a host"
        raise AnalyticsConfigError(msg)
    if parts.username is not None or parts.password is not None:
        msg = "ANALYTICS_ENDPOINT must not embed credentials (user:pass@host)"
        raise AnalyticsConfigError(msg)
    if parts.scheme == "http" and host not in _LOOPBACK:
        logger.warning(
            "ANALYTICS_ENDPOINT uses plain http to %s; events cross the network in clear", host
        )
    return endpoint


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """The resolved settings."""

    enabled: bool = True
    backend: str = "none"
    endpoint: str = ""
    site_id: str = ""
    hostname: str = ""
    consent: str = "implied"
    respect_gpc: bool = True
    geo: str = "none"
    """Accepted for contract parity. Backend events carry `ip=server`, so no geo is derived."""
    prop_keys: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> AnalyticsConfig:
        """Read the settings.

        Raises `AnalyticsConfigError` for any unknown value (`_ENABLED`, `_BACKEND`, `_CONSENT`,
        `_GEO`, `_RESPECT_GPC`) and for an unusable `ANALYTICS_ENDPOINT`. Call it at startup
        to fail fast.
        """
        keys = os.environ.get("ANALYTICS_PROP_KEYS", "")
        return cls(
            enabled=_flag("ANALYTICS_ENABLED", default=True),
            backend=_choice("ANALYTICS_BACKEND", BACKENDS, "none"),
            endpoint=_endpoint(),
            site_id=os.environ.get("ANALYTICS_SITE_ID", "").strip(),
            hostname=os.environ.get("ANALYTICS_HOSTNAME", "").strip(),
            consent=_choice("ANALYTICS_CONSENT", CONSENTS, "implied"),
            respect_gpc=_flag("ANALYTICS_RESPECT_GPC", default=True),
            geo=_choice("ANALYTICS_GEO", GEOS, "none"),
            prop_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
        )
