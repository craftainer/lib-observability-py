# 0003. Add a vendor-neutral `analytics` extra

## Status

Accepted

## Context

The BFF/analytics plan (2026-10) gives every backend of the template family one
`track()` contract (I3), so product events reach Umami (or any other sink) without the
application naming a vendor. Python services (template-fastapi) need it here, in the
library that already owns logging and OpenTelemetry. Constraints:

- Analytics must be cookieless and carry no personal data by default, honour the
  Global Privacy Control (GPC) and Do-Not-Track signals, and offer an opt-in mode.
- A request must never fail or wait because analytics is down (A10).
- Umami v3.4.1's `/api/batch` silently drops events whose `User-Agent` looks like a bot
  (httpx's default does), and answers HTTP 200 with an `errors` count even when events
  were rejected (source: `src/app/api/batch/route.ts`, `src/lib/detect.ts`,
  <https://github.com/umami-software/umami/tree/v3.4.1>).
- The core of this package needs FastAPI only; HTTP delivery needs `httpx`, which the
  `http` extra already brings (httpx documents `Client(timeout=, headers=, transport=)`
  and `MockTransport`: <https://www.python-httpx.org/>).

## Decision

- Add an `analytics` extra (`httpx>=0.28.1,<1`, mirroring `http`) and a
  `craftainer_observability.analytics` package. `track(name, props=None, *, url=None)`
  follows I3 exactly, including the `ANALYTICS_*` environment variables.
- Invalid names and properties are dropped and logged (at debug/warning, key only, never
  a value); `track()` never raises. Properties must be scalars on the
  `ANALYTICS_PROP_KEYS` allowlist (empty by default, so nothing is sent unless the app
  opts each key in); strings are truncated to 200 characters; `url` is reduced to its
  path.
- Adapters: `none`, `log` (a structured log line), `otel` (a log record on a dedicated
  logger, exported by the OTLP log bridge `configure_logging()` already installs, so
  the library adds no OpenTelemetry code of its own) and `umami`.
- Only `umami` is queued: a bounded queue (1000, overflow drops the new event) drained
  by a daemon thread every 5 s into batches of at most 50; 2 s HTTP timeout; the
  constant `payload.ip = "server"`, no `payload.id`, no title, the User-Agent
  `Mozilla/5.0 (compatible; craftainer-py)`; a non-200 status or a non-zero `errors` is
  logged as a warning with counts only. The in-process adapters are cheap and are called
  inline.
- Request-level privacy is a `contextvars`-based `request_context(gpc=, consent=)`
  that a framework's middleware sets (the library imports no web framework). With
  `ANALYTICS_RESPECT_GPC` true (default) `track()` is a no-op under `gpc=True`; with
  `ANALYTICS_CONSENT=opt-in` it is a no-op unless the context says `consent=True`
  (and when no context is active).
- `ANALYTICS_GEO` is parsed for contract parity but has no effect: backend events carry
  `ip=server`, so Umami derives no location from them.

- `ANALYTICS_ENDPOINT` must be the final URL: redirects are not followed, and a 3xx
  batch is dropped with one warning naming the status and `Location` (never the payload).
- `log`/`otel` events are flat log attributes (`analytics_event`, `analytics_url`,
  `analytics_prop_<key>`), because OTel log attributes must be primitives.
- After `fork()` the child resets the client's queue, locks and thread handle
  (`os.register_at_fork`) and starts its own flusher on the first `track()`.

## Consequences

- Services get one call that is safe in request handlers and works from sync and async
  code, with a vendor swap being an env change.
- Delivery is best effort: events are lost on overflow, a crash, or an unreachable
  Umami, and a final flush runs at process exit. That is the intended trade for never
  affecting a request.
- Privacy depends on the middleware setting `request_context`; calling `track()` outside
  a request context cannot see GPC. Visitor-level identity is deliberately absent from
  backend events (all share `ip=server`), so unique-visitor counts come from the BFF
  (which sees the client), not from here.
- The wheel gains no new dependency for users who do not install the extra.
