"""Analytics: validation, privacy gates, queueing, Umami batching, failure isolation."""

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from craftainer_observability import analytics
from craftainer_observability.analytics import adapters
from craftainer_observability.analytics import client as client_mod
from craftainer_observability.analytics.config import AnalyticsConfig
from craftainer_observability.analytics.validate import clean_props, clean_url, valid_name


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    """reset."""
    analytics.shutdown()
    yield
    analytics.shutdown()


class Recorder:
    """A fake Umami: records requests, answers with a canned response."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        """init  ."""
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = (
            {"size": 1, "processed": 1, "errors": 0, "details": []} if body is None else body
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """call  ."""
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        """Transport."""
        return httpx.MockTransport(self)

    def items(self) -> list[dict[str, Any]]:
        """Items."""
        return [i for r in self.requests for i in json.loads(r.content)]


def umami(rec: Recorder) -> analytics.AnalyticsClient:
    """Umami."""
    cfg = AnalyticsConfig(
        backend="umami",
        endpoint="http://umami:3000",
        site_id="site-1",
        hostname="app.example",
        prop_keys=frozenset({"plan", "n"}),
    )
    return analytics.AnalyticsClient(cfg, transport=rec.transport, flush_interval=3600)


def test_validate_name() -> None:
    """Validate name."""
    assert valid_name("signup.completed")
    assert valid_name("a.b_c.d1")
    for bad in ["signup", "Signup.done", "a." + "b" * 60, "a..b", "1a.b", "a.b!", "", 5, None]:
        assert not valid_name(bad)


def test_clean_props_allowlist_types_truncation() -> None:
    """Clean props allowlist types truncation."""
    allowed = frozenset({"a", "b", "c", "d", "e"})
    out = clean_props(
        {"a": "x" * 300, "b": 1, "c": True, "d": float("nan"), "e": [1], "zz": "no", 3: "k"},
        allowed,
    )
    assert out == {"a": "x" * 200, "b": 1, "c": True}
    assert clean_props(None, allowed) == {}
    assert clean_props({"a": 1}, frozenset()) == {}
    assert clean_props("nope", allowed) == {}
    assert clean_props({"a": 1.5}, allowed) == {"a": 1.5}


def test_clean_url() -> None:
    """Clean url."""
    assert clean_url("/a/b?x=1#frag") == "/a/b"
    assert clean_url("https://h.example/p?q=1") == "/p"
    assert clean_url("rel") == "/rel"
    assert clean_url("?only=query") == "/"
    assert clean_url(None) == "/"
    assert clean_url("") == "/"
    assert clean_url(5) == "/"  # type: ignore[arg-type]
    assert len(clean_url("/" + "a" * 900)) == 500


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config from env."""
    assert AnalyticsConfig.from_env() == AnalyticsConfig()
    for k, v in {
        "ANALYTICS_ENABLED": "false",
        "ANALYTICS_BACKEND": "UMAMI",
        "ANALYTICS_ENDPOINT": " http://u:3000/ ",
        "ANALYTICS_SITE_ID": "s",
        "ANALYTICS_HOSTNAME": "h",
        "ANALYTICS_CONSENT": "opt-in",
        "ANALYTICS_RESPECT_GPC": "0",
        "ANALYTICS_GEO": "coarse",
        "ANALYTICS_PROP_KEYS": "a, b,,",
    }.items():
        monkeypatch.setenv(k, v)
    cfg = AnalyticsConfig.from_env()
    assert cfg == AnalyticsConfig(
        enabled=False, backend="umami", endpoint="http://u:3000", site_id="s", hostname="h",
        consent="opt-in", respect_gpc=False, geo="coarse", prop_keys=frozenset({"a", "b"}),
    )  # fmt: skip


def test_config_unknown_choice_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Config unknown choice falls back."""
    monkeypatch.setenv("ANALYTICS_BACKEND", "bogus")
    with caplog.at_level(logging.WARNING):
        assert AnalyticsConfig.from_env().backend == "none"
    assert "bogus" in caplog.text


def test_umami_payload_and_headers() -> None:
    """Umami payload and headers."""
    rec = Recorder()
    c = umami(rec)
    c.track("signup.completed", {"plan": "pro", "secret": "x"}, url="/pricing?utm=1#f")
    assert rec.requests == []  # queued, not sent inline
    c.flush()
    (req,) = rec.requests
    assert str(req.url) == "http://umami:3000/api/batch"
    assert req.headers["user-agent"] == "Mozilla/5.0 (compatible; craftainer-py)"
    (item,) = json.loads(req.content)
    payload = item["payload"]
    assert item["type"] == "event"
    assert payload["website"] == "site-1"
    assert payload["hostname"] == "app.example"
    assert payload["url"] == "/pricing"
    assert payload["name"] == "signup.completed"
    assert payload["data"] == {"plan": "pro"}
    assert payload["ip"] == "server"
    assert abs(payload["timestamp"] - time.time()) < 5
    assert "id" not in payload
    assert "title" not in payload
    c.shutdown()


def test_umami_omits_empty_data_and_hostname() -> None:
    """Umami omits empty data and hostname."""
    rec = Recorder()
    c = analytics.AnalyticsClient(
        AnalyticsConfig(backend="umami", endpoint="http://u", site_id="s"),
        transport=rec.transport,
        flush_interval=3600,
    )
    c.track("a.b")
    c.flush()
    payload = rec.items()[0]["payload"]
    assert "data" not in payload
    assert "hostname" not in payload
    c.shutdown()


def test_invalid_name_dropped_and_logged_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid name dropped and logged without values."""
    rec = Recorder()
    c = umami(rec)
    with caplog.at_level(logging.DEBUG):
        c.track("BadName", {"plan": "secretvalue"})
        c.track(123)  # type: ignore[arg-type]
    c.flush()
    assert rec.requests == []
    assert "secretvalue" not in caplog.text
    assert "BadName" not in caplog.text
    c.shutdown()


def test_batches_split_at_50() -> None:
    """Batches split at 50."""
    rec = Recorder()
    c = umami(rec)
    for _ in range(120):
        c.track("a.b")
    c.flush()
    assert [len(json.loads(r.content)) for r in rec.requests] == [50, 50, 20]
    c.shutdown()


def test_queue_overflow_drops() -> None:
    """Queue overflow drops."""
    rec = Recorder()
    c = umami(rec)
    for _ in range(client_mod.QUEUE_CAPACITY + 25):
        c.track("a.b")
    c.flush()
    assert len(rec.items()) == client_mod.QUEUE_CAPACITY
    c.shutdown()


def test_errors_in_200_response_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Errors in 200 response logged."""
    rec = Recorder(body={"size": 1, "processed": 0, "errors": 1, "details": []})
    c = umami(rec)
    c.track("a.b")
    with caplog.at_level(logging.WARNING):
        c.flush()
    assert "reported 1 errors" in caplog.text
    c.shutdown()


def test_non_200_logged_and_later_chunks_still_sent(caplog: pytest.LogCaptureFixture) -> None:
    """Non 200 logged and later chunks still sent."""
    rec = Recorder(status=500)
    c = umami(rec)
    for _ in range(51):
        c.track("a.b")
    with caplog.at_level(logging.WARNING):
        c.flush()
    assert len(rec.requests) == 2
    assert "HTTP 500" in caplog.text
    c.shutdown()


def test_timeout_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    """Timeout never raises."""

    def boom(request: httpx.Request) -> httpx.Response:
        """Boom."""
        raise httpx.ConnectTimeout("slow", request=request)

    c = analytics.AnalyticsClient(
        AnalyticsConfig(backend="umami", endpoint="http://u", site_id="s"),
        transport=httpx.MockTransport(boom),
        flush_interval=3600,
    )
    c.track("a.b")
    with caplog.at_level(logging.DEBUG):
        c.flush()
    assert "ConnectTimeout" in caplog.text
    c.shutdown()


def test_umami_timeout_is_two_seconds() -> None:
    """Umami timeout is two seconds."""
    ad = adapters.UmamiAdapter(endpoint="http://u/", site_id="s", hostname="")
    assert ad._http.timeout.read == 2.0
    ad.close()


def test_background_thread_flushes() -> None:
    """Background thread flushes."""
    rec = Recorder()
    c = analytics.AnalyticsClient(
        AnalyticsConfig(backend="umami", endpoint="http://u", site_id="s"),
        transport=rec.transport,
        flush_interval=0.01,
    )
    c.track("a.b")
    c.track("a.c")  # second call reuses the thread
    deadline = time.monotonic() + 3
    while not rec.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rec.requests
    c.shutdown()
    c.track("a.d")  # after shutdown no new thread starts
    assert c._thread is not None
    assert not c._thread.is_alive()


def test_umami_misconfigured_is_off(caplog: pytest.LogCaptureFixture) -> None:
    """Umami misconfigured is off."""
    with caplog.at_level(logging.WARNING):
        c = analytics.AnalyticsClient(AnalyticsConfig(backend="umami"))
    c.track("a.b")
    assert "needs ANALYTICS_ENDPOINT" in caplog.text
    c.shutdown()


def test_disabled_and_none_are_noops() -> None:
    """Disabled and none are noops."""
    for cfg in (AnalyticsConfig(enabled=False, backend="log"), AnalyticsConfig(backend="none")):
        c = analytics.AnalyticsClient(cfg)
        c.track("a.b")
        c.shutdown()


@pytest.mark.parametrize(
    ("backend", "logger_name"),
    [
        ("log", "craftainer_observability.analytics.events"),
        ("otel", "craftainer_observability.analytics.otel"),
    ],
)
def test_log_and_otel_adapters(
    backend: str, logger_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Log and otel adapters."""
    c = analytics.AnalyticsClient(AnalyticsConfig(backend=backend, prop_keys=frozenset({"k"})))
    with caplog.at_level(logging.INFO, logger=logger_name):
        c.track("a.b", {"k": 1}, url="/x?y")
    (rec,) = [r for r in caplog.records if r.name == logger_name]
    assert rec.analytics_event == "a.b"  # type: ignore[attr-defined]
    assert rec.analytics_props == {"k": 1}  # type: ignore[attr-defined]
    assert rec.analytics_url == "/x"  # type: ignore[attr-defined]
    c.shutdown()


def test_gpc_context_blocks_and_can_be_ignored(caplog: pytest.LogCaptureFixture) -> None:
    """Gpc context blocks and can be ignored."""
    logname = "craftainer_observability.analytics.events"
    respect = analytics.AnalyticsClient(AnalyticsConfig(backend="log"))
    ignore = analytics.AnalyticsClient(AnalyticsConfig(backend="log", respect_gpc=False))
    with caplog.at_level(logging.INFO, logger=logname):
        with analytics.request_context(gpc=True):
            assert analytics.get_request_context() == analytics.RequestContext(gpc=True)
            respect.track("a.b")
            assert not caplog.records
            ignore.track("a.b")
            assert len(caplog.records) == 1
        assert analytics.get_request_context() is None
        respect.track("a.b")
        assert len(caplog.records) == 2


def test_opt_in_requires_consent(caplog: pytest.LogCaptureFixture) -> None:
    """Opt in requires consent."""
    logname = "craftainer_observability.analytics.events"
    c = analytics.AnalyticsClient(AnalyticsConfig(backend="log", consent="opt-in"))
    with caplog.at_level(logging.INFO, logger=logname):
        c.track("a.b")  # no request context
        with analytics.request_context():
            c.track("a.b")
        assert not caplog.records
        with analytics.request_context(consent=True):
            c.track("a.b")
        assert len(caplog.records) == 1
        with analytics.request_context(consent=True, gpc=True):
            c.track("a.b")
        assert len(caplog.records) == 1


def test_adapter_exception_never_escapes(caplog: pytest.LogCaptureFixture) -> None:
    """Adapter exception never escapes."""

    class Boom:
        """Boom."""

        def send(self, events: list[adapters.Event]) -> None:
            """Send."""
            raise RuntimeError("x")

    c = analytics.AnalyticsClient(AnalyticsConfig(backend="log"))
    c._adapter = Boom()
    with caplog.at_level(logging.DEBUG):
        c.track("a.b")
    assert "analytics track failed" in caplog.text


def test_module_track_uses_env_and_survives_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Module track uses env and survives errors."""
    monkeypatch.setenv("ANALYTICS_BACKEND", "log")
    with caplog.at_level(logging.INFO, logger="craftainer_observability.analytics.events"):
        analytics.track("a.b")
        analytics.track("a.b", url="/p")
    assert len(caplog.records) == 2

    def broken() -> None:
        """Broken."""
        raise RuntimeError("x")

    monkeypatch.setattr(client_mod, "get_client", broken)
    analytics.track("a.b")  # must not raise
