"""Unit test: the shared redaction rule set every sink uses."""

import pytest

from craftainer_observability.redaction import (
    REDACTED,
    REDACTION_KEYS,
    is_sensitive_key,
    redact_text,
    redact_value,
)


def test_key_list_is_the_agreed_one() -> None:
    """The list is the one `lib-observability-rs` uses verbatim."""
    assert REDACTION_KEYS == (
        "token", "password", "passwd", "pwd", "secret", "authorization", "claims",
        "credential", "api_key", "apikey", "access_key", "cookie", "bearer", "jwt",
        "session", "dsn", "private", "signature",
    )  # fmt: skip


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "Access_Token",
        "db_password",
        "client-secret",
        "X-Api-Key",
        "api_key",
        "Authorization",
        "Set-Cookie",
        "session_id",
        "sentry_dsn",
        "X-Amz-Signature",
        "jwt",
        "claims",
        "bearer",
        "private_key",
        "PrivateKey",
        "access-key",
        "apikey",
        "credentials",
        "pwd",
    ],
)
def test_sensitive_keys(key: str) -> None:
    """Keys that name a credential match, however cased or separated."""
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["request_id", "path", "status", "design", "user"])
def test_harmless_keys(key: str) -> None:
    """Ordinary field names are left alone."""
    assert not is_sensitive_key(key)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("postgres://u:p@db/x", "postgres://[REDACTED]@db/x"),
        ("session_id=abc x-api-key: k", "session_id=[REDACTED] x-api-key: [REDACTED]"),
        ("cookie=a dsn=b", "cookie=[REDACTED] dsn=[REDACTED]"),
        ("sent Bearer abc.def-123 here", "sent Bearer [REDACTED] here"),
        ("login eyJhbGciOi.eyJzdWIi.sig_x ok", "login [REDACTED] ok"),
        ("nothing to see", "nothing to see"),
    ],
)
def test_redact_text(text: str, expected: str) -> None:
    """URL userinfo, key/value pairs, bearer credentials and JWTs are hidden."""
    assert redact_text(text) == expected


def test_redact_value_by_key_and_by_content() -> None:
    """A sensitive key hides any value; other values are scrubbed by content."""
    assert redact_value("password", {"a": 1}) == REDACTED
    assert redact_value("x", None) is None
    assert redact_value("x", 3) == 3
    assert redact_value("x", True) is True
    assert redact_value("x", "password=hunter2") == "password=[REDACTED]"
    assert redact_value("x", RuntimeError("dsn=abc")) == "dsn=[REDACTED]"


def test_redact_value_walks_containers_to_a_depth_limit() -> None:
    """Nested mappings and sequences are scrubbed; beyond four levels they are replaced."""
    value = {"ok": [1, "token=a", {"cookie": "c", "n": 2}], 5: ("x",)}
    assert redact_value("data", value) == {
        "ok": [1, "token=[REDACTED]", {"cookie": REDACTED, "n": 2}],
        "5": ["x"],
    }
    deep: object = "leaf"
    for _ in range(6):
        deep = {"k": deep}
    assert "leaf" not in str(redact_value("data", deep))
