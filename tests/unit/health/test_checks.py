"""Unit test: each concrete health check's success/failure paths, services faked out."""

from collections.abc import Callable

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.health.checks import (
    DatabaseHealthCheck,
    MockHealthCheck,
    OIDCHealthCheck,
    RedisHealthCheck,
    S3HealthCheck,
)


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
    """DatabaseHealthCheck reports healthy when the query succeeds."""
    result = await DatabaseHealthCheck(_SucceedingEngine()).check()  # type: ignore[arg-type]
    assert result.healthy is True


async def test_database_check_reports_unhealthy_on_failure() -> None:
    """DatabaseHealthCheck reports unhealthy, with detail, when the query fails."""
    result = await DatabaseHealthCheck(_FailingEngine()).check()  # type: ignore[arg-type]
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
    """RedisHealthCheck reports healthy when PING succeeds."""
    monkeypatch.setattr(Redis, "from_url", _fake_redis_from_url(fail=False))
    result = await RedisHealthCheck("redis://example").check()
    assert result.healthy is True


async def test_redis_check_reports_unhealthy_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """RedisHealthCheck reports unhealthy, with detail, when PING fails."""
    monkeypatch.setattr(Redis, "from_url", _fake_redis_from_url(fail=True))
    result = await RedisHealthCheck("redis://example").check()
    assert result.healthy is False
    assert result.detail is not None


class _FakeS3Client:
    """Stand-in for a boto3 S3 client, with a scripted list_buckets outcome."""

    def __init__(self, *, fail: bool) -> None:
        """Remember whether list_buckets() should raise."""
        self._fail = fail

    def list_buckets(self) -> None:
        """Raise if scripted to fail, otherwise pretend to succeed."""
        if self._fail:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "ListBuckets")


def _fake_boto3_client(*, fail: bool) -> Callable[..., _FakeS3Client]:
    """Build a boto3.client replacement returning a client scripted to succeed/fail."""
    return lambda *args, **kwargs: _FakeS3Client(fail=fail)


async def test_s3_check_reports_healthy_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3HealthCheck reports healthy when ListBuckets succeeds."""
    monkeypatch.setattr(boto3, "client", _fake_boto3_client(fail=False))
    result = await S3HealthCheck("http://example", "ak", "sk").check()
    assert result.healthy is True


async def test_s3_check_reports_unhealthy_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3HealthCheck reports unhealthy, with detail, when ListBuckets fails."""
    monkeypatch.setattr(boto3, "client", _fake_boto3_client(fail=True))
    result = await S3HealthCheck("http://example", "ak", "sk").check()
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
    """OIDCHealthCheck reports healthy when the discovery document is reachable."""
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client(fail=False))
    result = await OIDCHealthCheck("http://issuer").check()
    assert result.healthy is True


async def test_oidc_check_reports_unhealthy_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """OIDCHealthCheck reports unhealthy, with detail, when the discovery fetch fails."""
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client(fail=True))
    result = await OIDCHealthCheck("http://issuer").check()
    assert result.healthy is False
    assert result.detail is not None


async def test_mock_health_check_always_reports_healthy() -> None:
    """MockHealthCheck reports healthy without touching the network."""
    result = await MockHealthCheck("database").check()
    assert result.name == "database"
    assert result.healthy is True
    assert result.detail == "mocked"
