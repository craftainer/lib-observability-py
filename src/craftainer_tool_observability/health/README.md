# health/

The generic health check framework: the check interface, a registry
that runs a set of checks concurrently, and a router factory exposing
`/live` (liveness — no dependency checks) and `/ready` (readiness — runs
every registered check). No external dependency beyond FastAPI itself —
this package carries no code for any specific service (Postgres, Redis,
S3, OIDC, ...); those concrete checks and the registry that wires them
up live in `app.health_checks` instead (see `../app/README.md`), the
same split as `crud/`'s generic framework vs. `app/`'s resource-specific
code.

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

## Do

- Keep this package's only import beyond the standard library and
  FastAPI itself at zero — a concrete, service-specific check (with its
  own client-library dependency) belongs in `app.health_checks`
  instead, built on `HealthCheck`/`HealthCheckResult` from here.

## Don't

- Add a concrete, service-specific `HealthCheck` implementation here —
  that's resource-agnostic-in-name only; a real check always depends on
  some specific external service's client library, which belongs in
  `app.health_checks`, not this package.
- Let one check's failure raise out of `HealthRegistry.run_all` —
  that's `app.health_checks`'s job (each concrete check catches its own
  service's exceptions and returns an unhealthy `HealthCheckResult`
  instead), not something this generic collector enforces itself.
