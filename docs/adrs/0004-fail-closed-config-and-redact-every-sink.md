# 0004. Fail closed on analytics config and redact every telemetry sink

## Status

Accepted

## Context

The OWASP review of 2026-11-04 (OW-lib-observability-py) found, in this library and in
`lib-observability-rs`, that:

- An unknown `ANALYTICS_CONSENT` (`optin`) fell back to `implied`, and an unknown
  `ANALYTICS_ENABLED` (`disabled`) left the kill switch on, so a typo tracked visitors
  without consent. `ANALYTICS_ENDPOINT` was not validated, and a delivery failure was
  logged at DEBUG only, so a typo made analytics silently dead.
- Credential redaction existed only in the stdout JSON formatter. The OTLP log bridge
  exported the raw record, neither sink redacted the message or the traceback, and the
  OTLP span exporter sent attributes as they were. The ASGI instrumentation records the
  request query string in `http.url` and `http.target` (a test on `/x?token=abc` confirms
  it), so `?token=` and `?code=` reached the trace backend. The health module used a
  different, shorter key list.
- `/ready` ran every dependency check per request with no cache, and `S3Check` ran
  `ListBuckets` through `asyncio.to_thread`: a call that outlived the check timeout kept
  its worker in the loop's default executor, which also serves DNS lookups.
- The package gave no way to read the `Sec-GPC`, `DNT` and `Analytics-Consent` headers.

## Decision

- `AnalyticsConfig.from_env()` raises `AnalyticsConfigError` (a `ValueError`) for an
  unknown `ANALYTICS_ENABLED`, `_BACKEND`, `_CONSENT`, `_GEO` or `_RESPECT_GPC` (accepted
  booleans: `1/true/yes/on`, `0/false/no/off`) and for an `ANALYTICS_ENDPOINT` that is not an http(s) URL with a
  host and without embedded credentials. Plain `http` to a non-loopback host logs a
  warning. Unknown configuration is fail-closed, matching `lib-observability-rs`. An application calls `from_env()` at startup to fail fast; `track()` itself
  never raises, so with an invalid configuration it tracks nothing and logs the cause at
  ERROR once. A failed delivery logs a WARNING (with the number of dropped events) at the
  first failure of an outage and DEBUG until a delivery succeeds again.
- One key list, `redaction.REDACTION_KEYS`, drives every sink (`lib-observability-rs`
  mirrors it). A key is sensitive when its lowercased form (`-` read as `_`) contains
  an entry: token, password, passwd, pwd, secret, authorization, claims, credential,
  api_key, apikey, access_key, cookie, bearer, jwt, session, dsn, private, signature. The text rules are URL userinfo, `key=value` pairs, a bare `Bearer` credential
  and a bare JWT. `health.redact` is the same rule set.
- Redaction is record-level. `logs.RedactingFilter` sits on both the stdout and the OTLP
  handler and returns a redacted copy (message with its arguments applied, `extra`
  fields, stack info, the traceback and the OTel `exception.*` attributes), leaving the
  shared record, and a host's own handlers, untouched. It never raises into the caller of
  `logger.*`: if redacting a record fails, the record is replaced by a blank one. The stdout
  JSON formatter does not redact a second time (`_JSONFormatter(redact=False)`), because the
  filter on the same handler has; used alone it still redacts. `otel.RedactingSpanExporter` wraps
  the OTLP span exporter: span and event names, attribute keys and values, exception events,
  links and the status description are redacted (the dropped counts are kept; a span that
  cannot be redacted is exported empty), and the query string is cut from `http.url`,
  `url.full`, `http.target` and `url.path`; `url.query` is dropped.
- `/ready` caches its result for `ready_cache_ttl` seconds (default 1, `0` disables) and
  concurrent requests share one run of the checks; the cache and a per-event-loop lock belong
  to the router, so `get_registry` must return the application's one registry. `S3Check`
  runs `ListBuckets` on its own single-thread executor and keeps one loop-independent call
  in flight: a later check awaits that call instead of starting another. `close()` is
  idempotent and later checks report unhealthy.
- The text rules are linear on hostile input (userinfo is scanned per token; key tails and
  JWT parts are length-bounded). `Authorization: <scheme> <credential>` and Digest-style
  parameters are hidden whole. The `token` entry also hides counters such as
  `gen_ai.usage.input_tokens`; that over-redaction is deliberate and shared with the Rust lib. The router belongs on an internal port.
- `analytics.from_headers(headers)` returns the `RequestContext` for `Sec-GPC: 1` or
  `DNT: 1` (`gpc`) and `Analytics-Consent: 1` (`consent`). Only the BFF may set
  `Analytics-Consent`.

## Consequences

- A misspelled consent or kill-switch value stops analytics (and is visible) instead of
  tracking; deployments that relied on the old fallback must fix their environment. This
  is a minor, behaviour-changing release (0.x).
- Over-redaction is accepted: any key containing `session`, `dsn` or `token` is hidden,
  and a message that contains a `key=value` pair for one is edited. Redaction runs per
  record and per exported span, which costs a few regular expressions each.
- `/ready` may be up to `ready_cache_ttl` seconds stale. A hung S3 call makes the S3 check
  report unhealthy until it returns, rather than adding threads.
- The redaction key list and patterns are now a contract with `lib-observability-rs`:
  change both together.
