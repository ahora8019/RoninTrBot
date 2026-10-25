from __future__ import annotations

import json
from io import BytesIO

from app.internal_api import create_app


def request(app, path="/v1/health", *, query_string="", method="GET", authorization="Bearer test-secret", body=None, extra_headers=None):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    raw_body = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {
        "CONTENT_LENGTH": str(len(raw_body)),
        "CONTENT_TYPE": "application/json",
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(raw_body),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    if authorization is not None:
        environ["HTTP_AUTHORIZATION"] = authorization
    if extra_headers:
        environ.update(extra_headers)
    body = b"".join(app(environ, start_response))
    payload = json.loads(body) if body else None
    return captured["status"], captured["headers"], payload


def test_health_is_authenticated_and_reports_build_metadata():
    app = create_app(secret="test-secret", version="1.2.3", commit="abc1234")

    status, headers, payload = request(app)

    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["ok"] is True
    assert payload["request_id"]
    assert payload["error"] is None
    assert payload["data"] == {
        "status": "ok",
        "version": "1.2.3",
        "commit": "abc1234",
        "stale_after_seconds": 60,
    }


def test_rejects_missing_or_invalid_bearer_token():
    app = create_app(secret="test-secret")

    for authorization in (None, "Basic test-secret", "Bearer wrong"):
        status, _, payload = request(app, authorization=authorization)
        assert status == "401 Unauthorized"
        assert payload["ok"] is False
        assert payload["data"] is None
        assert payload["error"]["code"] == "unauthorized"
        assert "test-secret" not in json.dumps(payload)


def test_platform_health_probe_is_available_without_internal_secret():
    app = create_app(secret="")

    status, _, payload = request(
        app, path="/healthz", authorization=None
    )

    assert status == "200 OK"
    assert payload["ok"] is True
    assert payload["data"] == {"status": "ok"}
    assert payload["error"] is None


def test_fails_closed_when_internal_secret_is_not_configured():
    app = create_app(secret="")

    status, _, payload = request(app, authorization="Bearer anything")

    assert status == "503 Service Unavailable"
    assert payload["error"]["code"] == "service_not_configured"


def test_status_is_explicitly_paper_only_and_does_not_claim_engine_is_running():
    app = create_app(secret="test-secret")

    status, _, payload = request(app, path="/v1/status")

    assert status == "200 OK"
    assert payload["data"]["trading_mode"] == "paper"
    assert payload["data"]["live_trading_enabled"] is False
    assert payload["data"]["engine_state"] == "not_started"


def test_unknown_routes_and_non_get_methods_are_rejected():
    app = create_app(secret="test-secret")

    status, _, payload = request(app, path="/v1/not-real")
    assert status == "404 Not Found"
    assert payload["error"]["code"] == "not_found"

    status, _, payload = request(app, method="POST")
    assert status == "405 Method Not Allowed"
    assert payload["error"]["code"] == "method_not_allowed"


def test_request_id_is_generated_per_request():
    app = create_app(secret="test-secret")

    _, _, first = request(app)
    _, _, second = request(app)

    assert first["request_id"]
    assert second["request_id"]
    assert first["request_id"] != second["request_id"]


def test_market_ohlcv_endpoint_returns_normalized_lbank_candles():
    from decimal import Decimal
    from app.market_data.lbank import Candle

    class FakeMarketDataClient:
        def fetch_ohlcv(self, symbol, interval, limit):
            assert (symbol, interval, limit) == ("btc_usdt", "15m", 2)
            return [
                Candle(1_800_000_000, Decimal("100"), Decimal("110"), Decimal("90"), Decimal("105"), Decimal("12.5"))
            ]

    app = create_app(secret="test-secret", market_data_client=FakeMarketDataClient())
    status, headers, payload = request(
        app, path="/v1/market/btc_usdt/ohlcv", query_string="interval=15m&limit=2"
    )

    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    assert payload["ok"] is True
    assert payload["data"]["symbol"] == "btc_usdt"
    assert payload["data"]["interval"] == "15m"
    assert payload["data"]["candles"] == [{
        "timestamp": 1_800_000_000,
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "12.5",
    }]


def test_market_ohlcv_requires_interval_and_rejects_invalid_symbol():
    app = create_app(secret="test-secret")

    status, _, payload = request(app, path="/v1/market/btc_usdt/ohlcv")
    assert status == "422 Unprocessable Entity"
    assert payload["error"]["code"] == "invalid_market_request"

    status, _, payload = request(
        app, path="/v1/market/../ohlcv", query_string="interval=15m"
    )
    assert status in ("404 Not Found", "422 Unprocessable Entity")
    assert payload["ok"] is False


def test_market_ohlcv_maps_upstream_failure_to_safe_bad_gateway():
    from app.market_data.lbank import MarketDataError

    class BrokenMarketDataClient:
        def fetch_ohlcv(self, symbol, interval, limit):
            raise MarketDataError("private upstream detail")

    app = create_app(secret="test-secret", market_data_client=BrokenMarketDataClient())
    status, _, payload = request(
        app, path="/v1/market/btc_usdt/ohlcv", query_string="interval=15m"
    )

    assert status == "502 Bad Gateway"
    assert payload["error"]["code"] == "market_data_unavailable"
    assert "private upstream detail" not in json.dumps(payload)


def test_strategy_analysis_endpoint_returns_explainable_lbank_signal():
    from decimal import Decimal
    from app.market_data.lbank import Candle

    class FakeMarketDataClient:
        def fetch_ohlcv(self, symbol, interval, limit):
            assert (symbol, interval, limit) == ("btc_usdt", "1h", 200)
            return [
                Candle(1_800_000_000 + i * 3600,
                       Decimal(100 + i), Decimal(102 + i), Decimal(99 + i),
                       Decimal(101 + i), Decimal("10"))
                for i in range(60)
            ]

    app = create_app(secret="test-secret", market_data_client=FakeMarketDataClient())
    status, headers, payload = request(
        app, path="/v1/strategy/analysis", query_string="symbol=btc_usdt&interval=1h"
    )

    assert status == "200 OK"
    assert headers["Cache-Control"] == "no-store"
    assert payload["ok"] is True
    assert payload["data"]["source"] == "lbank"
    assert payload["data"]["symbol"] == "btc_usdt"
    assert payload["data"]["interval"] == "1h"
    assert payload["data"]["direction"] == "long"
    assert payload["data"]["score"] >= 70
    assert payload["data"]["eligible"] is True
    assert payload["data"]["reasons"]


def test_strategy_analysis_rejects_missing_or_unsupported_interval():
    app = create_app(secret="test-secret")

    status, _, payload = request(app, path="/v1/strategy/analysis", query_string="symbol=btc_usdt")
    assert status == "422 Unprocessable Entity"
    assert payload["error"]["code"] == "invalid_strategy_request"

    status, _, payload = request(
        app, path="/v1/strategy/analysis", query_string="symbol=btc_usdt&interval=1m"
    )
    assert status == "422 Unprocessable Entity"
    assert payload["error"]["code"] == "invalid_strategy_request"


def test_strategy_analysis_maps_insufficient_history_to_safe_bad_gateway():
    from app.market_data.lbank import MarketDataError

    class BrokenMarketDataClient:
        def fetch_ohlcv(self, symbol, interval, limit):
            raise MarketDataError("private upstream detail")

    app = create_app(secret="test-secret", market_data_client=BrokenMarketDataClient())
    status, _, payload = request(
        app, path="/v1/strategy/analysis", query_string="symbol=btc_usdt&interval=15m"
    )

    assert status == "502 Bad Gateway"
    assert payload["error"]["code"] == "market_data_unavailable"
    assert "private upstream detail" not in json.dumps(payload)



def test_paper_status_reports_not_started_until_session_runs():
    app = create_app(secret="test-secret")
    status, _, payload = request(app, path="/v1/paper/status")

    assert status == "200 OK"
    assert payload["data"]["mode"] == "paper"
    assert payload["data"]["live_trading_enabled"] is False
    assert payload["data"]["session_state"] == "not_started"


def test_paper_session_is_lab_only_and_rejects_missing_idempotency_key():
    app = create_app(secret="test-secret")
    status, _, payload = request(
        app, path="/v1/paper/session", method="POST",
        body={"policy": "trend", "candles_ref": "lbank:btc_usdt:1h:200"},
    )
    assert status == "403 Forbidden"
    assert payload["error"]["code"] == "paper_lab_only"

    import os
    old = os.environ.get("RONINTRBOT_LAB_MODE")
    os.environ["RONINTRBOT_LAB_MODE"] = "true"
    try:
        app = create_app(secret="test-secret")
        status, _, payload = request(
            app, path="/v1/paper/session", method="POST",
            body={"policy": "trend", "candles_ref": "lbank:btc_usdt:1h:200"},
        )
        assert status == "422 Unprocessable Entity"
        assert payload["error"]["code"] == "idempotency_key_required"
    finally:
        if old is None:
            os.environ.pop("RONINTRBOT_LAB_MODE", None)
        else:
            os.environ["RONINTRBOT_LAB_MODE"] = old


def test_paper_session_replays_completed_result_without_refetching_market_data(monkeypatch, tmp_path):
    from decimal import Decimal
    from app.market_data.lbank import Candle

    monkeypatch.setenv("RONINTRBOT_LAB_MODE", "true")
    monkeypatch.setenv("RONINTRBOT_PAPER_DB_PATH", str(tmp_path / "paper.sqlite3"))

    class FakeMarketDataClient:
        calls = 0

        def fetch_ohlcv(self, symbol, interval, limit):
            self.calls += 1
            assert (symbol, interval, limit) == ("btc_usdt", "1h", 60)
            return [
                Candle(1_800_000_000 + i * 3600, Decimal(100 + i),
                       Decimal(102 + i), Decimal(99 + i), Decimal(101 + i), Decimal("10"))
                for i in range(60)
            ]

    client = FakeMarketDataClient()
    app = create_app(secret="test-secret", market_data_client=client)
    body = {"policy": "trend", "candles_ref": "lbank:btc_usdt:1h:60"}
    headers = {"HTTP_IDEMPOTENCY_KEY": "paper-key-123"}

    status, _, first = request(app, path="/v1/paper/session", method="POST", body=body, extra_headers=headers)
    status2, _, replay = request(app, path="/v1/paper/session", method="POST", body=body, extra_headers=headers)

    assert status == status2 == "200 OK"
    assert first["data"]["idempotency_storage"] == "sqlite_durable_lab_only"
    assert replay["data"] == first["data"]
    assert client.calls == 1


def test_paper_session_rejects_same_key_with_different_payload(monkeypatch, tmp_path):
    from decimal import Decimal
    from app.market_data.lbank import Candle

    monkeypatch.setenv("RONINTRBOT_LAB_MODE", "true")
    monkeypatch.setenv("RONINTRBOT_PAPER_DB_PATH", str(tmp_path / "paper.sqlite3"))

    class FakeMarketDataClient:
        def fetch_ohlcv(self, symbol, interval, limit):
            return [
                Candle(1_800_000_000 + i * 3600, Decimal(100 + i),
                       Decimal(102 + i), Decimal(99 + i), Decimal(101 + i), Decimal("10"))
                for i in range(60)
            ]

    app = create_app(secret="test-secret", market_data_client=FakeMarketDataClient())
    headers = {"HTTP_IDEMPOTENCY_KEY": "paper-key-456"}
    status, _, _ = request(
        app, path="/v1/paper/session", method="POST",
        body={"policy": "trend", "candles_ref": "lbank:btc_usdt:1h:60"}, extra_headers=headers,
    )
    conflict, _, payload = request(
        app, path="/v1/paper/session", method="POST",
        body={"policy": "trend", "candles_ref": "lbank:btc_usdt:4h:60"}, extra_headers=headers,
    )

    assert status == "200 OK"
    assert conflict == "409 Conflict"
    assert payload["error"]["code"] == "idempotency_conflict"


def test_paper_session_releases_reservation_after_unexpected_failure(monkeypatch, tmp_path):
    from decimal import Decimal
    from app.market_data.lbank import Candle

    monkeypatch.setenv("RONINTRBOT_LAB_MODE", "true")
    monkeypatch.setenv("RONINTRBOT_PAPER_DB_PATH", str(tmp_path / "paper.sqlite3"))

    class FlakyMarketDataClient:
        calls = 0

        def fetch_ohlcv(self, symbol, interval, limit):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("private diagnostic detail")
            return [
                Candle(1_800_000_000 + i * 3600, Decimal(100 + i),
                       Decimal(102 + i), Decimal(99 + i), Decimal(101 + i), Decimal("10"))
                for i in range(60)
            ]

    client = FlakyMarketDataClient()
    app = create_app(secret="test-secret", market_data_client=client)
    headers = {"HTTP_IDEMPOTENCY_KEY": "paper-key-789"}
    body = {"policy": "trend", "candles_ref": "lbank:btc_usdt:1h:60"}

    failed, _, error = request(app, path="/v1/paper/session", method="POST", body=body, extra_headers=headers)
    retried, _, result = request(app, path="/v1/paper/session", method="POST", body=body, extra_headers=headers)

    assert failed == "502 Bad Gateway"
    assert error["error"]["code"] == "paper_session_failed"
    assert "private diagnostic detail" not in json.dumps(error)
    assert retried == "200 OK"
    assert result["data"]["mode"] == "paper"
    assert client.calls == 2


def test_platform_root_head_probe_is_available_without_internal_secret():
    app = create_app(secret="")

    status, headers, payload = request(
        app, path="/", method="HEAD", authorization=None
    )

    assert status == "200 OK"
    assert int(headers["Content-Length"]) > 0
    assert payload is None
