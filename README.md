# craftainer-observability

> [!NOTE]
> This repository is vibe coded with [Claude](https://claude.com/product/claude-code).

Observability building blocks for FastAPI services: a health check framework
with `/live` and `/ready` routes and generic dependency checks, structured
JSON logging, OpenTelemetry traces and metrics, a request-ID middleware and
opt-in request profiling. It is extracted from
[`template-fastapi`](https://github.com/craftainer/template-fastapi) and is an
instance of
[`template-python-package`](https://github.com/craftainer/template-python-package)
(see [`docs/TEMPLATE.md`](docs/TEMPLATE.md) for the conventions shared with
every instance). It never imports an application package or
`craftainer_crud`; an import-linter contract enforces that.

## Install

The distribution is `craftainer-observability`, the import package
`craftainer_observability`. The core needs FastAPI only; everything else
is an extra, so install what you use:

```sh
pip install craftainer-observability                    # health framework, request IDs
pip install "craftainer-observability[otel,redis]"      # plus tracing/metrics/logs, Redis check
```

> [!NOTE]
> Renamed from `craftainer-tool-observability` (import
> `craftainer_tool_observability`); the first release under the new name is
> `0.2.0`. Install `craftainer-observability` and import
> `craftainer_observability`. See
> [ADR 0001](docs/adrs/0001-rename-package-to-craftainer-observability.md).

| Extra | Brings | Used by |
| --- | --- | --- |
| `otel` | OpenTelemetry SDK, OTLP/HTTP exporters, logging, FastAPI and SQLAlchemy instrumentations (and SQLAlchemy, which `otel.py` imports for typing) | `logs.py`, `otel.py` |
| `sqlalchemy` | SQLAlchemy (asyncio) | `health.checks.postgres.PostgresCheck` |
| `redis` | redis-py | `health.checks.redis.RedisCheck` |
| `s3` | boto3 | `health.checks.s3.S3Check` |
| `http` | httpx | `health.checks.http.HTTPCheck` |
| `profiling` | pyinstrument | `profiling.install_profiling` |

`logs.py` and `otel.py` import OpenTelemetry at module level, so import them
only when `[otel]` is installed. The package root, `health` and `middleware`
import with no extra installed (a unit test proves it). Note that
`opentelemetry-sqlalchemy` instrumentation supports SQLAlchemy below 2.1 only,
hence the `<2.1` bound.

## Public API

| Module | Provides |
| --- | --- |
| `health` | `HealthCheck`, `HealthCheckResult`, `HealthRegistry`, `build_health_router(get_registry, include_info=False, info_package=None)` |
| `health.checks` | `PostgresCheck`, `RedisCheck`, `S3Check`, `HTTPCheck` (one module each) |
| `logs` | `configure_logging()`, `shared_resource()` |
| `otel` | `configure_telemetry()`, `instrument_app(app)`, `instrument_engine(engine)` |
| `middleware` | `RequestIDMiddleware`, `get_request_id()` |
| `profiling` | `install_profiling(app, *, enabled, production, query_param="profile")` |

The details, environment variables and conventions are in
[`src/craftainer_observability/README.md`](src/craftainer_observability/README.md).

## Versioning

SemVer, starting at `0.1.0`. While the major version is 0, a minor release may
change the public API; pin `craftainer-observability>=0.x,<0.(x+1)` until
1.0. Real-service integration tests for the dependency checks stay in
`template-fastapi`; the unit tests here use fakes and need no backing service.

## Development

```sh
uv sync
prek run --all-files --hook-stage manual   # lint, types, import contracts, unit tests (100% coverage)
uv run pytest tests/bench --no-cov         # benchmarks (CodSpeed in CI)
```

## Publishing to PyPI

Releases use PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/)
(GitHub OIDC): no API token is stored anywhere. `make publish-packages` skips
with a warning until you opt in, per repository:

1. Register a "pending publisher" on the index before the first release
   (PyPI: Your account, Publishing, "Add a new pending publisher"; TestPyPI
   has its own, separate form on test.pypi.org). Enter the project name from
   `pyproject.toml`, the GitHub owner and repository, the workflow name
   `release.yml`, and leave the environment empty (or use the one
   `release.yml` runs under, if you add one).
2. Set the repository variable `PYPI_PUBLISH` (Settings, Secrets and
   variables, Actions, Variables):
   - unset: `make publish-packages` warns that it skipped and the release only
     attaches the sdist, wheel and SBOM to the GitHub release;
   - `testpypi`: publish to TestPyPI;
   - `pypi`: publish to PyPI.
3. Do the first release with `testpypi` as a dry run (a pre-release channel
   is enough), confirm that the release workflow's `verify-published` job (its last
   job; it runs `make verify-published` after the GitHub release exists, so a
   failure does not undo the release) installs the published version back
   from TestPyPI. The target needs the release's `dist/` and
   `RELEASE_VERSION`, so run it only in that workflow, not locally. Then
   register the PyPI pending publisher and switch the variable to `pypi`.

The version lives only in the git tag `release.yml` computes; `pyproject.toml`
stays at `0.0.0` and `make build` stamps `RELEASE_VERSION` into the build
without committing it.

## License

Licensed under the [GNU General Public License v3.0](LICENSE).
