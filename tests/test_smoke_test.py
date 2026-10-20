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
        "gitSha": "a" * 40,
        "status": "online",
        "engine": "idle",
        "paperTrading": True,
        "webhookAuth": "configured",
    }


def mock_health_and_probe(monkeypatch, payload=None, probe_status=403):
    body = json.dumps(payload if payload is not None else valid_payload()).encode()

    def fake_urlopen(request, timeout=10.0):
        if request.get_method() == "POST":
            return FakeResponse(b"", probe_status)
        return FakeResponse(body)

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", fake_urlopen)


def test_smoke_accepts_valid_health_and_rejected_unauthenticated_probe(monkeypatch):
    mock_health_and_probe(monkeypatch)
    assert smoke_test.smoke("https://example.invalid") == []


def test_smoke_accepts_exact_expected_git_sha(monkeypatch):
    mock_health_and_probe(monkeypatch)
    assert smoke_test.smoke("https://example.invalid", expected_sha="a" * 40) == []


def test_smoke_rejects_mismatched_expected_git_sha(monkeypatch):
    mock_health_and_probe(monkeypatch)
    failures = smoke_test.smoke("https://example.invalid", expected_sha="b" * 40)
    assert any("does not match expected SHA" in failure for failure in failures)


@pytest.mark.parametrize("git_sha", ["unknown", "", None, "a" * 39, "g" * 40, "A" * 40])
def test_smoke_rejects_missing_or_invalid_deployed_git_sha(monkeypatch, git_sha):
    payload = valid_payload()
    payload["gitSha"] = git_sha
    mock_health_and_probe(monkeypatch, payload)
    failures = smoke_test.smoke("https://example.invalid")
    assert any("gitSha is missing, unknown" in failure for failure in failures)


@pytest.mark.parametrize("expected_sha", ["unknown", "a" * 39, "g" * 40, "A" * 40])
def test_smoke_rejects_invalid_expected_git_sha_before_network_call(monkeypatch, expected_sha):
    def unexpected_network_call(*args, **kwargs):
        pytest.fail("invalid expected SHA must be rejected before making a request")

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", unexpected_network_call)
    assert smoke_test.smoke("https://example.invalid", expected_sha=expected_sha) == [
        "expected Git SHA must be exactly 40 lowercase hexadecimal characters"
    ]


@pytest.mark.parametrize("body, expected", [
    (b"\xff", "UnicodeDecodeError"),
    (b"{broken", "JSONDecodeError"),
])
def test_smoke_reports_malformed_response_without_crashing(monkeypatch, body, expected):
    monkeypatch.setattr(
        smoke_test.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(body),
    )
    failures = smoke_test.smoke("https://example.invalid")
    assert len(failures) == 1
    assert expected in failures[0]


@pytest.mark.parametrize("body", [b"null", b"[]", b'"string"'])
def test_smoke_rejects_non_object_health_payload(monkeypatch, body):
    monkeypatch.setattr(
        smoke_test.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(body),
    )
    assert smoke_test.smoke("https://example.invalid") == [
        "health response must be a JSON object"
    ]


def test_smoke_reports_network_error(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(smoke_test.urllib.request, "urlopen", fail)
    failures = smoke_test.smoke("https://example.invalid")
    assert len(failures) == 1
    assert "URLError" in failures[0]


@pytest.mark.parametrize("auth_state", ["not_configured", None, "unknown"])
def test_smoke_fails_closed_when_webhook_secret_is_not_configured(
    monkeypatch, auth_state
):
    payload = valid_payload()
    payload["webhookAuth"] = auth_state
    mock_health_and_probe(monkeypatch, payload)
    failures = smoke_test.smoke("https://example.invalid")
    assert any("expected 'configured'" in failure for failure in failures)


@pytest.mark.parametrize("status", [200, 201, 404, 500])
def test_smoke_fails_if_unauthenticated_webhook_probe_is_not_forbidden(
    monkeypatch, status
):
    mock_health_and_probe(monkeypatch, probe_status=status)
    failures = smoke_test.smoke("https://example.invalid")
    assert any("expected 403" in failure for failure in failures)


def test_smoke_rejects_non_online_health_state(monkeypatch):
    payload = valid_payload()
    payload["status"] = "offline"
    mock_health_and_probe(monkeypatch, payload)
    assert any(
        "expected 'online'" in failure
        for failure in smoke_test.smoke("https://example.invalid")
    )
