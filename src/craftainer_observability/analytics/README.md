# analytics/

`track(name, props=None, *, url=None)`: vendor-neutral product analytics ([ADR 0003](../../../docs/adrs/0003-add-vendor-neutral-analytics-extra.md)).
Needs the `[analytics]` extra (`httpx`). Never raises and never blocks the caller.

- `validate.py` — event name (`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, at most 50 chars), allowlisted scalar props (values at most 200 chars), path-only `url`.
- `config.py` — `AnalyticsConfig.from_env()`: `ANALYTICS_ENABLED`, `_BACKEND` (`none|log|umami|otel`), `_ENDPOINT`, `_SITE_ID`, `_HOSTNAME`, `_CONSENT` (`implied|opt-in`), `_RESPECT_GPC`, `_GEO`, `_PROP_KEYS`. It fails closed
  ([ADR 0004](../../../docs/adrs/0004-fail-closed-config-and-redact-every-sink.md)): an unknown `_ENABLED`, `_BACKEND`, `_CONSENT`, `_GEO` or `_RESPECT_GPC` (booleans: `1/true/yes/on`, `0/false/no/off`), and an
  `_ENDPOINT` that is not an http(s) URL with a host and no `user:pass@`, raise
  `AnalyticsConfigError`. Call `AnalyticsConfig.from_env()` at startup to fail fast; `track()`
  never raises, so with an invalid configuration it tracks nothing and logs the cause at ERROR
  once. Plain `http` to a non-loopback host warns.
- `context.py` — `request_context(gpc=, consent=)`, a `ContextVar` a web framework's middleware sets per request; no framework import. `from_headers(headers)` turns a header mapping (case-insensitive) into the `RequestContext`: `Sec-GPC: 1` or `DNT: 1` sets `gpc`, `Analytics-Consent: 1` sets `consent`; anything else is no signal.
- `adapters.py` — `NoneAdapter`, `LogAdapter`, `OtelAdapter`, `UmamiAdapter` (`POST {ENDPOINT}/api/batch`, at most 50 per request, 2 s timeout, `payload.ip` is the constant `server`).
- `client.py` — `AnalyticsClient` and the process-wide `track()` / `shutdown()`; the Umami queue holds 1000 events, flushes every 5 s and drops on overflow. A failed delivery logs a WARNING with the dropped count at the first failure of an outage, then DEBUG until a delivery succeeds.

## Do

- Set `ANALYTICS_ENDPOINT` to the final URL: redirects are not followed (a 3xx batch is dropped with a warning naming the `Location`).
- Wrap each request in `request_context(...)` from the framework's middleware, e.g. `ctx = from_headers(request.headers)`, then `request_context(gpc=ctx.gpc, consent=ctx.consent)`.
- Let only the BFF set `Analytics-Consent` and strip it from untrusted traffic: a service reachable without the BFF would accept a forged consent.
- Add a prop key to `ANALYTICS_PROP_KEYS` before sending it.

## Don't

- Import a web framework here, or log the value of a dropped or invalid prop (the `log`/`otel` adapters do log allowlisted values by design).
