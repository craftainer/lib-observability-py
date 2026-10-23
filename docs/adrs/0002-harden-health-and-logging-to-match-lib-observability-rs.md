# 0002. Harden health checks and logging, in step with lib-observability-rs

## Status

Accepted

## Context

The AX2 review of `lib-observability-rs` (2026-10-22) found gaps in the Rust port
and fixed them there (PR #3, ADR 0003 of that repository). Several were
deviations from this library, and the Rust port's rule was "match
`lib-observability-py`, and where Python is demonstrably wrong, fix Python too". Five
gaps are real here as well:

- Only `HTTPCheck` had a timeout, so a hung Postgres, Redis or S3 stalled `/ready`.
- `HealthRegistry.register` appended a duplicate name, and `/ready` (which keys its
  response by name) then collapsed the results and hid a failing check.
- `logger.exception` logged the raw exception and traceback, which can embed a DSN.
- `configure_logging` added a second set of handlers when called twice, so every line
  printed twice.
- `RedisCheck` opened and closed a client on every probe.

## Decision

- The registry wraps every check in a timeout (5 seconds by default, per registry or
  per `register` call). A timeout reports the check unhealthy with a fixed detail and
  logs the real cause.
- `register` raises `ValueError` for a duplicate name.
- Checks log through `log_failure`, which redacts URL userinfo and secret key/value
  pairs (the patterns of `lib-observability-rs`) from the message and traceback.
- `configure_logging` removes the handlers it installed earlier before adding its own,
  and creates the OTLP log provider once per process.
- `RedisCheck` keeps one client for its lifetime and gains `aclose()`.

## Consequences

`register` raising on a duplicate is a breaking change for a caller that registers
the same name twice (it used to be silently accepted). This is a 0.x library, so it
ships in a minor release, and the caller must give each check a distinct `name=`.

The timeout cancels the check's coroutine; an S3 check's thread (`asyncio.to_thread`)
cannot be cancelled and runs on until boto3 returns, though `/ready` no longer waits
for it. The reused Redis client belongs to one event loop. Redaction is a safety net
that over-redacts (a URL path containing `@` is cut), not a parser.
