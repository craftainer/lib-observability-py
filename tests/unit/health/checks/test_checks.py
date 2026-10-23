"""Unit test: each generic dependency check's success/failure paths, services faked out."""

import logging
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from craftainer_observability.health.checks.http import HTTPCheck
from craftainer_observability.health.checks.postgres import PostgresCheck
from craftainer_observability.health.checks.redis import RedisCheck
from craftainer_observability.health.checks.s3 import S3Check


class _FakeConnection:
    """Stand-in for an AsyncConnection: an async context manager with a no-op execute."""

    async def __aenter__(self) -> _FakeConnection:
        """Enter the fake connection context."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the fake connection context, swallowing nothing."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        """Pretend to run the given statement."""


class _FailingEngine:
    """Stand-in for an AsyncEngine whose connect() always fails."""

    def connect(self) -> _FakeConnection:
        """Raise, as a real engine would if the database were unreachable."""
        raise SQLAlchemyError("connection refused")


class _SucceedingEngine:
    """Stand-in for an AsyncEngine whose connect() always succeeds."""

    def connect(self) -> _FakeConnection:
        """Return a fake connection that answers any statement."""
        return _FakeConnection()


async def test_database_check_reports_healthy_on_success() -> None:
    """PostgresCheck reports healthy when the query succeeds."""
    result = await PostgresCheck(_SucceedingEngine()).check()  # type: ignore[arg-type]
    assert result.healthy is True


async def test_database_check_reports_unhealthy_on_failure() -> None:
    """PostgresCheck reports unhealthy, with detail, when the query fails."""
    result = await PostgresCheck(_FailingEngine()).check()  # type: ignore[arg-type]
    assert result.healthy is False
    assert result.detail is not None


class _FakeRedisClient:
    """Stand-in for redis.asyncio.Redis, with a scripted ping outcome."""

    def __init__(self, *, fail: bool) -> None:
        """Remember whether ping() should raise."""
        self._fail = fail

    async def ping(self) -> bool:
        """Raise if scripted to fail, otherwise report success."""
        if self._fail:
            raise RedisError("connection refused")
        return True

    async def aclose(self) -> None:
        """Pretend to close the connection."""


def _fake_redis_from_url(*, fail: bool) -> Callable[[str], _FakeRedisClient]:
    """Build a from_url replacement returning a client scripted to succeed/fail."""
    return lambda _url: _FakeRedisClient(fail=fail)


async def test_redis_check_reports_healthy_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """RedisCheck reports healthy when PING succeeds."""
    monkeypatch.setattr(Redis, "from_url", _fake_redis_from_url(fail=False))
    result = await RedisCheck("redis://example").check()
    assert result.healthy is True


async def test_redis_check_reports_unhealthy_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """RedisCheck reports unhealthy, with detail, when PING fails."""
    monkeypatch.setattr(Redis, "from_url", _fake_redis_from_url(fail=True))
    result = await RedisCheck("redis://example").check()
    assert result.healthy is False
    assert result.detail is not None


async def test_redis_check_reuses_one_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """RedisCheck builds its client once and keeps it until aclose()."""
    created: list[_FakeRedisClient] = []

    def from_url(_url: str) -> _FakeRedisClient:
        created.append(_FakeRedisClient(fail=False))
        return created[-1]

    monkeypatch.setattr(Redis, "from_url", from_url)
    check = RedisCheck("redis://example")
    await check.check()
    await check.check()
    assert len(created) == 1
    await check.aclose()
    await check.aclose()
    await check.check()
    assert len(created) == 2


async def test_failed_checks_log_without_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The logged error and traceback have URL userinfo and password pairs redacted."""

    class _Leaky(_FakeRedisClient):
        async def ping(self) -> bool:
            msg = "cannot reach redis://:hunter2@cache:6379/0 password=hunter3"
            raise RedisError(msg)

    monkeypatch.setattr(Redis, "from_url", lambda _url: _Leaky(fail=True))
    with caplog.at_level(logging.ERROR):
        result = await RedisCheck("redis://:hunter2@cache").check()
    assert result.healthy is False
    assert "Redis health check failed" in caplog.text
    assert "redis://[REDACTED]@cache:6379/0" in caplog.text
    assert "password=[REDACTED]" in caplog.text
    assert "hunter" not in caplog.text


class _FakeS3Client:
    """Stand-in for a boto3 S3 client, with a scripted list_buckets outcome."""

    def __init__(self, *, fail: bool) -> None:
        """Remember whether list_buckets() should raise."""
        self._fail = fail

    def list_buckets(self) -> None:
        """Raise if scripted to fail, otherwise pretend to succeed."""
        if self._fail:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "ListBuckets")


async def test_s3_check_reports_healthy_on_success() -> None:
    """S3Check reports healthy when ListBuckets succeeds."""
    result = await S3Check(cast("Any", _FakeS3Client(fail=False))).check()
    assert result.healthy is True


async def test_s3_check_reports_unhealthy_on_failure() -> None:
    """S3Check reports unhealthy, with detail, when ListBuckets fails."""
    result = await S3Check(cast("Any", _FakeS3Client(fail=True))).check()
    assert result.healthy is False
    assert result.detail is not None


class _FakeHTTPResponse:
    """Stand-in for an httpx.Response whose raise_for_status is a no-op."""

    def raise_for_status(self) -> None:
        """Pretend the response was successful."""


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient, with a scripted get() outcome."""

    def __init__(self, *, fail: bool) -> None:
        """Remember whether get() should raise."""
        self._fail = fail

    async def __aenter__(self) -> _FakeAsyncClient:
        """Enter the fake client context."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit the fake client context, swallowing nothing."""

    async def get(self, url: str) -> _FakeHTTPResponse:
        """Raise if scripted to fail, otherwise return a successful fake response."""
        if self._fail:
            raise httpx.ConnectError("connection refused")
        return _FakeHTTPResponse()


def _fake_async_client(*, fail: bool) -> Callable[..., _FakeAsyncClient]:
    """Build an httpx.AsyncClient replacement returning a client scripted to succeed/fail."""
    return lambda **kwargs: _FakeAsyncClient(fail=fail)


async def test_oidc_check_reports_healthy_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPCheck reports healthy when the URL is reachable."""
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client(fail=False))
    result = await HTTPCheck("http://issuer/x").check()
    assert result.healthy is True


async def test_oidc_check_reports_unhealthy_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPCheck reports unhealthy, with detail, when the GET fails."""
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client(fail=True))
    result = await HTTPCheck("http://issuer/x").check()
    assert result.healthy is False
    assert result.detail is not None


async def test_check_names_default_and_override() -> None:
    """Each check reports under a default name, overridable by the caller."""
    assert PostgresCheck(cast("Any", _SucceedingEngine())).name == "postgres"
    assert PostgresCheck(cast("Any", _SucceedingEngine()), name="database").name == "database"
    assert RedisCheck("redis://example").name == "redis"
    assert S3Check(cast("Any", _FakeS3Client(fail=False))).name == "s3"
    assert HTTPCheck("http://x").name == "http"
    assert HTTPCheck("http://x", name="oidc").name == "oidc"


async def test_failed_checks_all_log_redacted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Postgres, S3 and HTTP failures also log through the redacting helper."""

    class _Engine:
        def connect(self) -> _FakeConnection:
            msg = "bad postgres://u:hunter2@db/x"
            raise SQLAlchemyError(msg)

    class _S3:
        def list_buckets(self) -> None:
            raise ClientError({"Error": {"Code": "1", "Message": "password=hunter2"}}, "L")

    with caplog.at_level(logging.ERROR):
        await PostgresCheck(cast("Any", _Engine())).check()
        await S3Check(cast("Any", _S3())).check()
    assert "hunter" not in caplog.text
    assert caplog.text.count("[REDACTED]") >= 2


def test_http_check_exposes_its_timeout() -> None:
    """HTTPCheck.timeout is public so the registry honours it instead of its default."""
    assert HTTPCheck("http://x", timeout=12).timeout == 12
