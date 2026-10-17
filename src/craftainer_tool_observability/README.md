# craftainer_tool_observability/

Observability building blocks `app` wires in: `health/` (the generic health
check framework) and `logs.py` (structured logging), described below. Neither
imports from `app`.

## health/

The generic health check framework: the check interface, a registry
that runs a set of checks concurrently, and a router factory exposing
`/live` (liveness — no dependency checks) and `/ready` (readiness — runs
every registered check). The framework itself has no external dependency
beyond FastAPI; the generic checks for specific services live in
`health/checks/` (see "Dependency checks" below), and the registry that
wires them up from `Settings` lives in `app.health_checks` (see
`../app/README.md`).

Laid out as its own small MVC-ish split under one strict,
one-directional import order — a lower layer never imports from a
higher one — enforced by `import-linter`'s `"health layers"` contract
in `../../pyproject.toml`'s `[tool.importlinter]`, run via `uv run
lint-imports` (wired into `../../.pre-commit-config.yaml`'s
manual/pre-push stage, same as mypy).

- `base.py` — `HealthCheck`, the `Protocol` every check implements
  (a `name` and an async `check() -> HealthCheckResult`), and
  `HealthCheckResult` itself.
- `registry.py` — `HealthRegistry` (`register`/`run_all`, the latter
  running every check concurrently via `asyncio.gather`); a bare
  collector with no knowledge of what it's collecting.
- `router.py` — `build_health_router(get_registry)`, a factory that
  builds the `/live`/`/ready` router against whatever
  `Callable[..., HealthRegistry]` the caller passes in as a FastAPI
  dependency — see `app.main`'s `build_health_router(get_health_registry)`
  call for the wiring.

```mermaid
graph LR
    base --> registry --> router
```

An arrow means "may import from" — each module may depend on anything
to its left, never anything to its right.

## Dependency checks

`health/checks/` holds one generic check per dependency, each taking its
engine, URL or client as a constructor argument (never reading settings, never
importing `app` or `craftainer_tool_crud`) and each importing its client
library only inside its own module. A check's `name` defaults to the service
(`postgres`, `redis`, `s3`, `http`) and can be overridden with `name=`.

| Check | Module | Extra | Constructor |
| --- | --- | --- | --- |
| `PostgresCheck` | `checks/postgres.py` | `[sqlalchemy]` | `PostgresCheck(engine)`, an `AsyncEngine`; runs `SELECT 1` |
| `RedisCheck` | `checks/redis.py` | `[redis]` | `RedisCheck(url)`; PINGs |
| `S3Check` | `checks/s3.py` | `[s3]` | `S3Check(client)`, a boto3 S3 client; calls `ListBuckets` in a thread |
| `HTTPCheck` | `checks/http.py` | `[http]` | `HTTPCheck(url, timeout=5)`; GETs the full URL (e.g. an OIDC discovery document) |

Each catches its own client library's errors, logs them, and returns an
unhealthy `HealthCheckResult` carrying only a fixed detail string (the real
exception text can embed credentials, so it goes to the logs only; `/ready` is
unauthenticated).

## logs

`logs.py`'s `configure_logging()` attaches a JSON-formatting stdout handler to the
root logger (with credential-shaped `extra=` keys redacted) and, when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, bridges the root logger to an OTLP log
exporter. The OTel `service.name` comes from OTel's standard `OTEL_SERVICE_NAME`
env var (`Resource.create()` reads it itself); the module imports neither `app`
nor its settings. See `../app/README.md`'s "Structured logging / OTEL" section.

## Do

- Keep `health/`'s framework modules (`base`, `registry`, `router`) free of
  any import beyond the standard library and FastAPI; a service-specific
  check (with its own client library) goes in its own `health/checks/`
  module, built on `HealthCheck`/`HealthCheckResult`.

## Don't

- Add a service-specific `HealthCheck` outside `health/checks/`, or make a
  check read settings; wiring from `Settings` (and `MODE=mock` fakes) belongs
  in `app.health_checks`.
- Let one check's failure raise out of `HealthRegistry.run_all` —
  that's each check's job (it catches its own service's exceptions and
  returns an unhealthy `HealthCheckResult` instead), not something this
  generic collector enforces itself.
