from pathlib import Path
import importlib.util
import json
import re
import sys
import types


def _load_worker_module(monkeypatch):
    fake_workers = types.ModuleType("workers")

    class FakeResponse:
        def __init__(self, body, status, headers):
            self.body = body
            self.status = status
            self.headers = headers

        @classmethod
        def new(cls, body, status=200, headers=None):
            return cls(body, status, headers or {})

    fake_workers.Response = FakeResponse
    fake_workers.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
    monkeypatch.setitem(sys.modules, "workers", fake_workers)

    path = Path("workers/ronin-core-python/entry.py")
    spec = importlib.util.spec_from_file_location("ronin_python_worker_entry_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeRequest:
    def __init__(self, path, *, method="GET", authorization="Bearer correct-secret"):
        self.url = f"https://example.invalid{path}"
        self.method = method
        self.headers = {"authorization": authorization}


def _decode_response(response):
    return json.loads(response.body)


def test_python_worker_fails_closed_until_routes_are_migrated():
    source = Path("workers/ronin-core-python/entry.py").read_text(encoding="utf-8")
    assert "INTERNAL_API_SECRET" in source
    assert "hmac.compare_digest" in source
    assert 'supplied.encode("utf-8")' in source
    assert 'secret.encode("utf-8")' in source
    assert '"route_not_ready"' in source
    assert '"live_trading_enabled": False' in source
    assert "urlsplit(request.url)" in source


def test_python_worker_bearer_validation_handles_non_ascii_input(monkeypatch):
    module = _load_worker_module(monkeypatch)

    assert module._valid_bearer("Bearer correct-secret", "correct-secret") is True
    assert module._valid_bearer("Bearer wrong-secret", "correct-secret") is False
    assert module._valid_bearer("Bearer café", "correct-secret") is False
    assert module._valid_bearer("Basic correct-secret", "correct-secret") is False
    assert module._valid_bearer("Bearer ", "correct-secret") is False


def test_python_worker_rejects_unauthenticated_health_request(monkeypatch):
    module = _load_worker_module(monkeypatch)
    worker = module.Default()
    worker.env = types.SimpleNamespace(INTERNAL_API_SECRET="correct-secret")

    response = __import__("asyncio").run(
        worker.fetch(FakeRequest("/v1/health", authorization="Bearer wrong"))
    )

    assert response.status == 401
    assert _decode_response(response)["error"]["code"] == "unauthorized"


def test_python_worker_health_response_requires_secret_and_has_no_cache(monkeypatch):
    module = _load_worker_module(monkeypatch)
    worker = module.Default()
    worker.env = types.SimpleNamespace(
        INTERNAL_API_SECRET="correct-secret",
        RONINTRBOT_VERSION="0.1.0",
        GIT_SHA="abcdef1234567",
    )

    response = __import__("asyncio").run(worker.fetch(FakeRequest("/v1/health")))

    payload = _decode_response(response)
    assert response.status == 200
    assert payload["ok"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["commit"] == "abcdef1234567"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_python_worker_does_not_enable_unmigrated_routes_or_live_trading(monkeypatch):
    module = _load_worker_module(monkeypatch)
    worker = module.Default()
    worker.env = types.SimpleNamespace(INTERNAL_API_SECRET="correct-secret")

    market = __import__("asyncio").run(
        worker.fetch(FakeRequest("/v1/market/BTC_USDT/ohlcv"))
    )
    status = __import__("asyncio").run(worker.fetch(FakeRequest("/v1/status")))

    assert market.status == 503
    assert _decode_response(market)["error"]["code"] == "route_not_ready"
    status_payload = _decode_response(status)
    assert status_payload["data"]["live_trading_enabled"] is False
    assert status_payload["data"]["trading_mode"] == "paper"


def test_python_worker_config_has_python_runtime_without_unprovisioned_resources():
    raw = Path("workers/ronin-core-python/wrangler.jsonc").read_text(encoding="utf-8")
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
    config = json.loads(raw)
    assert config["main"] == "entry.py"
    assert "python_workers" in config["compatibility_flags"]
    assert config["name"] == "ronintrbot-python-core-preview"
    assert "d1_databases" not in config
    assert "REPLACE_WITH" not in raw


def test_python_worker_has_dedicated_pywrangler_project():
    project = Path("workers/ronin-core-python/pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.13"' in project
    assert '"workers-runtime-sdk"' in project
    assert '"workers-py"' in project


def test_d1_schema_has_primary_key_and_constrained_state():
    schema = Path("workers/ronin-core-python/migrations/0001_paper_idempotency.sql").read_text(encoding="utf-8")
    assert "idempotency_key TEXT PRIMARY KEY" in schema
    assert "state IN ('in_progress', 'completed')" in schema


def test_python_worker_treats_whitespace_only_secret_as_unconfigured(monkeypatch):
    module = _load_worker_module(monkeypatch)
    worker = module.Default()
    worker.env = types.SimpleNamespace(INTERNAL_API_SECRET="   \t  ")

    response = __import__("asyncio").run(
        worker.fetch(FakeRequest("/v1/health", authorization="Bearer anything"))
    )

    assert response.status == 503
    assert _decode_response(response)["error"]["code"] == "service_not_configured"
