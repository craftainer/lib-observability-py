"""Unit test: credential redaction for logged health errors (same cases as lib-observability-rs)."""

import pytest

from craftainer_observability.health.redact import redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "error connecting to postgres://app:hunter2@db:5432/app?sslmode=x",
            "error connecting to postgres://[REDACTED]@db:5432/app?sslmode=x",
        ),
        (
            "redis://:s3cret@cache:6379/0 and https://u:p@a.example/x",
            "redis://[REDACTED]@cache:6379/0 and https://[REDACTED]@a.example/x",
        ),
        (
            "postgres://app:pa/ss@db/x and postgres://u:p?w#z@h/d",
            "postgres://[REDACTED]@db/x and postgres://[REDACTED]@h/d",
        ),
        (
            "host=db user=app password=hunter2 dbname=x",
            "host=db user=app password=[REDACTED] dbname=x",
        ),
        (
            "PASSWORD: hunter2 token = 'a b c' and db_pwd=x&k=1",
            "PASSWORD: [REDACTED] token = [REDACTED] and db_pwd=[REDACTED]",
        ),
        ('secret_key="q r" end', "secret_key=[REDACTED] end"),
        ("api_key=abc apikey:def", "api_key=[REDACTED] apikey:[REDACTED]"),
        ("password=a;b,c)d&e done", "password=[REDACTED] done"),
        (r"token='it\'s secret' x", "token=[REDACTED] x"),
        ("password='oops", "password=[REDACTED]"),
    ],
)
def test_credentials_are_redacted(text: str, expected: str) -> None:
    """URL userinfo and secret key/value pairs become [REDACTED]."""
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "GET https://example.com/a failed: http://host:1/x",
        "bad url ://",
        "the token was rejected; password",
    ],
)
def test_text_without_credentials_is_untouched(text: str) -> None:
    """Plain URLs and prose that merely mention a secret word are left alone."""
    assert redact(text) == text
