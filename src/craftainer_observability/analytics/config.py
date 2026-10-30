"""Analytics settings, read from the `ANALYTICS_*` environment variables (contract I3)."""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BACKENDS = ("none", "log", "umami", "otel")
CONSENTS = ("implied", "opt-in")
GEOS = ("none", "coarse")


def _flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value not in allowed:
        logger.warning("%s=%r is not one of %s; using %r", name, value, allowed, default)
        return default
    return value


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
        """Read the settings; an unknown choice logs a warning and falls back to its default."""
        keys = os.environ.get("ANALYTICS_PROP_KEYS", "")
        return cls(
            enabled=_flag("ANALYTICS_ENABLED", default=True),
            backend=_choice("ANALYTICS_BACKEND", BACKENDS, "none"),
            endpoint=os.environ.get("ANALYTICS_ENDPOINT", "").strip().rstrip("/"),
            site_id=os.environ.get("ANALYTICS_SITE_ID", "").strip(),
            hostname=os.environ.get("ANALYTICS_HOSTNAME", "").strip(),
            consent=_choice("ANALYTICS_CONSENT", CONSENTS, "implied"),
            respect_gpc=_flag("ANALYTICS_RESPECT_GPC", default=True),
            geo=_choice("ANALYTICS_GEO", GEOS, "none"),
            prop_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
        )
