# analytics/

`track(name, props=None, *, url=None)`: vendor-neutral product analytics ([ADR 0003](../../../docs/adrs/0003-add-vendor-neutral-analytics-extra.md)).
Needs the `[analytics]` extra (`httpx`). Never raises and never blocks the caller.

- `validate.py` — event name (`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, at most 50 chars), allowlisted scalar props (values at most 200 chars), path-only `url`.
- `config.py` — `AnalyticsConfig.from_env()`: `ANALYTICS_ENABLED`, `_BACKEND` (`none|log|umami|otel`), `_ENDPOINT`, `_SITE_ID`, `_HOSTNAME`, `_CONSENT` (`implied|opt-in`), `_RESPECT_GPC`, `_GEO`, `_PROP_KEYS`.
- `context.py` — `request_context(gpc=, consent=)`, a `ContextVar` a web framework's middleware sets per request; no framework import.
- `adapters.py` — `NoneAdapter`, `LogAdapter`, `OtelAdapter`, `UmamiAdapter` (`POST {ENDPOINT}/api/batch`, at most 50 per request, 2 s timeout, `payload.ip` is the constant `server`).
- `client.py` — `AnalyticsClient` and the process-wide `track()` / `shutdown()`; the Umami queue holds 1000 events, flushes every 5 s and drops on overflow.

## Do

- Wrap each request in `request_context(...)` from the framework's middleware.
- Add a prop key to `ANALYTICS_PROP_KEYS` before sending it.

## Don't

- Import a web framework here, or log the value of a dropped or invalid prop (the `log`/`otel` adapters do log allowlisted values by design).
