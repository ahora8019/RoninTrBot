"""Small WSGI API for RoninTrBot's authenticated, read-only Python core.

The market-data endpoint uses LBank's public spot OHLCV API. No exchange API
key is required, and this module intentionally exposes no account/order routes.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import time
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs
from uuid import uuid4
from wsgiref.simple_server import make_server

from app.market_data.lbank import LBankMarketDataClient, MarketDataError


_JSON_HEADERS = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
]
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
_MARKET_PATH = re.compile(r"^/v1/market/([a-z0-9]+_[a-z0-9]+)/ohlcv$")
_ALLOWED_INTERVALS = {"15m", "1h", "4h"}
_MAX_MARKET_LIMIT = 1200


def _build_metadata(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


def create_app(
    *,
    secret: str | None = None,
    version: str | None = None,
    commit: str | None = None,
    market_data_client: Any | None = None,
) -> Callable:
    """Create a WSGI application; inject configuration/dependencies in tests."""
    configured_secret = (
        os.environ.get("INTERNAL_API_SECRET", "") if secret is None else secret
    )
    build_version = _build_metadata(
        version if version is not None else os.environ.get("RONINTRBOT_VERSION"),
        "0.1.0",
    )
    raw_commit = (
        commit
        if commit is not None
        else os.environ.get("GIT_SHA", os.environ.get("GITHUB_SHA", ""))
    )
    normalized_commit = raw_commit.strip() if raw_commit else ""
    build_commit = (
        normalized_commit
        if _SHA_PATTERN.fullmatch(normalized_commit)
        else "unknown"
    )
    lbank_client = market_data_client or LBankMarketDataClient()
    market_state: dict[str, Any] = {"last_fetch_at": None}

    def respond(
        start_response,
        status: HTTPStatus,
        request_id: str,
        *,
        data=None,
        error=None,
        extra_headers=(),
    ):
        body = json.dumps(
            {"ok": error is None, "request_id": request_id, "data": data, "error": error},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        headers = [
            *_JSON_HEADERS,
            ("Content-Length", str(len(body))),
            ("X-Request-ID", request_id),
            *extra_headers,
        ]
        start_response(f"{status.value} {status.phrase}", headers)
        return [body]

    def error_response(start_response, request_id, status, code, message):
        return respond(
            start_response,
            status,
            request_id,
            error={"code": code, "message": message},
        )

    def application(environ, start_response):
        request_id = str(uuid4())
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        if not configured_secret:
            return error_response(
                start_response,
                request_id,
                HTTPStatus.SERVICE_UNAVAILABLE,
                "service_not_configured",
                "Internal API authentication is not configured.",
            )

        authorization = environ.get("HTTP_AUTHORIZATION", "")
        scheme, separator, supplied = authorization.partition(" ")
        token_matches = separator and supplied and hmac.compare_digest(
            supplied.encode("utf-8"), configured_secret.encode("utf-8")
        )
        if scheme.lower() != "bearer" or not token_matches:
            return respond(
                start_response,
                HTTPStatus.UNAUTHORIZED,
                request_id,
                error={
                    "code": "unauthorized",
                    "message": "A valid bearer token is required.",
                },
                extra_headers=(("WWW-Authenticate", "Bearer"),),
            )

        if method != "GET":
            return respond(
                start_response,
                HTTPStatus.METHOD_NOT_ALLOWED,
                request_id,
                error={
                    "code": "method_not_allowed",
                    "message": "Only GET is supported by this API slice.",
                },
                extra_headers=(("Allow", "GET"),),
            )

        if path == "/v1/health":
            return respond(
                start_response,
                HTTPStatus.OK,
                request_id,
                data={
                    "status": "ok",
                    "version": build_version,
                    "commit": build_commit,
                    "stale_after_seconds": 60,
                },
            )

        if path == "/v1/status":
            return respond(
                start_response,
                HTTPStatus.OK,
                request_id,
                data={
                    "service": "ronintrbot-python-core",
                    "trading_mode": "paper",
                    "live_trading_enabled": False,
                    "engine_state": "not_started",
                    "market_data_state": (
                        "connected" if market_state["last_fetch_at"] is not None
                        else "not_connected"
                    ),
                    "last_market_fetch_at": market_state["last_fetch_at"],
                    "paper_engine_state": "not_started",
                },
            )

        market_match = _MARKET_PATH.fullmatch(path)
        if market_match:
            symbol = market_match.group(1)
            query = parse_qs(
                environ.get("QUERY_STRING", ""), keep_blank_values=True
            )
            interval_values = query.get("interval", [])
            limit_values = query.get("limit", ["200"])
            if (
                len(interval_values) != 1
                or interval_values[0] not in _ALLOWED_INTERVALS
                or len(limit_values) != 1
            ):
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_market_request",
                    "Provide one supported interval (15m, 1h, 4h) and a valid limit.",
                )
            try:
                limit = int(limit_values[0])
            except (TypeError, ValueError):
                limit = 0
            if not 1 <= limit <= _MAX_MARKET_LIMIT:
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_market_request",
                    f"limit must be an integer between 1 and {_MAX_MARKET_LIMIT}.",
                )

            interval = interval_values[0]
            try:
                candles = lbank_client.fetch_ohlcv(symbol, interval, limit)
            except MarketDataError:
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.BAD_GATEWAY,
                    "market_data_unavailable",
                    "LBank market data is temporarily unavailable or invalid.",
                )
            except ValueError:
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_market_request",
                    "The symbol, interval, or limit is invalid.",
                )

            market_state["last_fetch_at"] = int(time.time())
            return respond(
                start_response,
                HTTPStatus.OK,
                request_id,
                data={
                    "source": "lbank",
                    "market_type": "spot",
                    "symbol": symbol,
                    "interval": interval,
                    "count": len(candles),
                    "candles": [
                        {
                            "timestamp": candle.timestamp,
                            "open": str(candle.open),
                            "high": str(candle.high),
                            "low": str(candle.low),
                            "close": str(candle.close),
                            "volume": str(candle.volume),
                        }
                        for candle in candles
                    ],
                },
            )

        return error_response(
            start_response,
            request_id,
            HTTPStatus.NOT_FOUND,
            "not_found",
            "The requested endpoint is not available in this API version.",
        )

    return application


application = create_app()


if __name__ == "__main__":
    # Local development only. Production must terminate TLS at a trusted host/proxy.
    with make_server(
        "127.0.0.1", int(os.environ.get("PORT", "8080")), application
    ) as server:
        server.serve_forever()
