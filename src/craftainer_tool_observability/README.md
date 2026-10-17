# craftainer_tool_observability/

Observability building blocks `app` wires in: `health/` (the generic health
check framework), `logs.py` (structured logging) and `otel.py` (traces and
metrics), described below. None imports from `app`.

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

## Telemetry

`otel.py` adds traces and metrics next to `logs.py`'s log export (`[otel]` extra;
`[sqlalchemy]` for the engine part). The app calls three functions:
`configure_telemetry()` (tracer and meter providers, idempotent),
`instrument_app(app)` (server spans and request metrics) and
`instrument_engine(engine)` (query spans and connection-pool metrics). All three are
no-ops unless telemetry is configured. All three signals share one `Resource`
(`logs.shared_resource()`); metrics and traces are pushed over OTLP/HTTP, and there is
no `/metrics` endpoint. The SQLAlchemy instrumentation already emits pool usage
(`db.client.connections.usage`, `state` = `idle`/`used`), so there are no extra gauges.
Every JSON log line carries `trace_id`/`span_id` while a span is active.

Only OTel's own environment variables configure it (`ADR-0007`):

| Variable | Effect |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | The on switch. Unset means nothing is set up. |
| `OTEL_SDK_DISABLED=true` | Kill switch for all three signals. |
| `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER` / `OTEL_LOGS_EXPORTER` | `none` turns that one signal off (default `otlp`). |
| `OTEL_TRACES_SAMPLER` (+ `_ARG`) | Sampling, read by the SDK. |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metric push interval in ms, read by the SDK. |
| `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` | Routes left out of traces and metrics. Unset means `/health/.*`. |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | `service.name` and any other resource attribute. |
| `APP_VERSION` | `service.version`, unless `OTEL_RESOURCE_ATTRIBUTES` already sets it. |
| `OTEL_SEMCONV_STABILITY_OPT_IN=http` | Emit the stable `http.server.request.duration` instead of the legacy `http.server.duration`. |

### Scaling metric (KEDA)

Set `OTEL_RESOURCE_ATTRIBUTES=k8s.pod.name=$(POD_NAME)` on the pod, with `POD_NAME`
from the Downward API (`fieldRef: metadata.name`), so metrics can be split per pod.
After Prometheus's OTLP name translation `http.server.active_requests` becomes
`http_server_active_requests`. An example query for KEDA's Prometheus scaler (average
in-flight requests per pod):

```promql
avg(sum by (k8s_pod_name) (http_server_active_requests{service_name="app"}))
```

Set the scaler `threshold` to about 60-70% of the per-pod capacity measured at the
reference budget (1 vCPU / 2 GB).

### Canary analysis (per `service_version`)

Pass the release version as `APP_VERSION` (same value as the OCI version label); it
becomes `service.version`, which Prometheus exposes as `service_version`. With
`OTEL_SEMCONV_STABILITY_OPT_IN=http`, the 5xx error ratio and p95 latency per version are:

```promql
sum by (service_version) (rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
  / sum by (service_version) (rate(http_server_request_duration_seconds_count[5m]))

histogram_quantile(0.95, sum by (service_version, le) (rate(http_server_request_duration_seconds_bucket[5m])))
```

(Without the opt-in, the metric is `http_server_duration_milliseconds_*` and the status
label is `http_status_code`.)

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
