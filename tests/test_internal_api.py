from __future__ import annotations

import json
from io import BytesIO

from app.internal_api import create_app


def request(app, path="/v1/health", *, query_string="", method="GET", authorization="Bearer test-secret"):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    if authorization is not None:
        environ["HTTP_AUTHORIZATION"] = authorization
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], json.loads(body)


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
