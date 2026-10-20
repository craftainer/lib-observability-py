"""Microbenchmarks of the hot paths (pytest-benchmark / CodSpeed)."""

import asyncio
import json
import logging

from pytest_benchmark.fixture import BenchmarkFixture

from craftainer_observability.health.base import HealthCheckResult
from craftainer_observability.health.registry import HealthRegistry
from craftainer_observability.logs import _JSONFormatter


class _OkCheck:
    """A check that is always healthy."""

    def __init__(self, name: str) -> None:
        """Report under `name`."""
        self.name = name

    async def check(self) -> HealthCheckResult:
        """Return a healthy result."""
        return HealthCheckResult(self.name, healthy=True)


def test_health_registry_run_all_bench(benchmark: BenchmarkFixture) -> None:
    """Time evaluating a registry of 20 trivial checks."""
    registry = HealthRegistry()
    for i in range(20):
        registry.register(_OkCheck(f"c{i}"))

    def run() -> int:
        return len(asyncio.run(registry.run_all()))

    assert benchmark(run) == 20


def test_json_log_format_bench(benchmark: BenchmarkFixture) -> None:
    """Time formatting one structured log record with extras to JSON."""
    formatter = _JSONFormatter()
    record = logging.LogRecord("bench", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.user_id = 42
    record.password = "hunter2"  # noqa: S105 -- exercises the redaction path

    out = benchmark(formatter.format, record)
    assert json.loads(out)["message"] == "hello world"
