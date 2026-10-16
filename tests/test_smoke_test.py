import io
import json
import urllib.error

import pytest

from scripts import smoke_test


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def valid_payload():
    return {
        "service": "RoninTrBot",
        "version": "2.5.0",
        "status": "online",
        "engine": "idle",
        "paperTrading": True,
        "webhookAuth": "not_configured",
    }


def test_smoke_accepts_valid_health_payload(monkeypatch):
    body = json.dumps(valid_payload()).encode()
    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    assert smoke_test.smoke("https://example.invalid") == []


@pytest.mark.parametrize("body, expected", [
    (b"\xff", "UnicodeDecodeError"),
    (b"{broken", "JSONDecodeError"),
])
def test_smoke_reports_malformed_response_without_crashing(monkeypatch, body, expected):
    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    failures = smoke_test.smoke("https://example.invalid")
    assert len(failures) == 1
    assert expected in failures[0]


@pytest.mark.parametrize("body", [b"null", b"[]", b'"string"'])
def test_smoke_rejects_non_object_health_payload(monkeypatch, body):
    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    assert smoke_test.smoke("https://example.invalid") == ["health response must be a JSON object"]


def test_smoke_reports_network_error(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", fail)
    failures = smoke_test.smoke("https://example.invalid")
    assert len(failures) == 1
    assert "URLError" in failures[0]
