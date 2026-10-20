"""The core (package, health framework, request-ID middleware) needs FastAPI only."""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import sys

    # opentelemetry-api itself is a hard dependency of FastAPI, so only the
    # SDK, exporters and instrumentations (the `otel` extra) are blocked.
    BLOCKED = {
        "opentelemetry.sdk", "opentelemetry.exporter", "opentelemetry.instrumentation",
        "sqlalchemy", "redis", "boto3", "botocore", "httpx", "httpx2", "pyinstrument",
    }

    def blocked(name):
        return any(name == b or name.startswith(b + ".") for b in BLOCKED)

    class Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if blocked(name):
                raise ModuleNotFoundError(f"blocked: {name}", name=name)
            return None

    sys.meta_path.insert(0, Blocker())

    import craftainer_observability
    import craftainer_observability.health
    import craftainer_observability.health.base
    import craftainer_observability.health.registry
    import craftainer_observability.health.router
    import craftainer_observability.middleware
    import craftainer_observability.health.checks

    assert not [m for m in sys.modules if blocked(m)]
    """
)


def test_core_imports_without_any_extra() -> None:
    """Importing the package, `.health` and the middleware pulls in no optional library."""
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
