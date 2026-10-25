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
from decimal import Decimal
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs
from uuid import uuid4
from wsgiref.simple_server import make_server

from app.market_data.lbank import LBankMarketDataClient, MarketDataError
from app.strategy.indicators import Candle as StrategyCandle, SIGNAL_THRESHOLD, analyze_timeframe
from app.paper.portfolio import PaperConfig, PaperEngine
from app.paper.runner import run_paper_session
from app.paper.store import SQLiteIdempotencyStore
from app.risk.engine import RiskLimits
from app.strategy.policy import TrendPolicy


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
    paper_state: dict[str, Any] = {"last_session_at": None, "session_count": 0}
    # Idempotency is stored in SQLite; hosted deployments must mount a persistent volume.
    # Session counters below intentionally remain process-local.
    paper_store = SQLiteIdempotencyStore(
        os.environ.get("RONINTRBOT_PAPER_DB_PATH", ".data/ronintrbot-paper.sqlite3")
    )

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

        if method != "GET" and not (method == "POST" and path == "/v1/paper/session"):
            return respond(
                start_response,
                HTTPStatus.METHOD_NOT_ALLOWED,
                request_id,
                error={
                    "code": "method_not_allowed",
                    "message": "Only GET is supported, except POST /v1/paper/session.",
                },
                extra_headers=(("Allow", "GET, POST" if path == "/v1/paper/session" else "GET"),),
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
                    "paper_engine_state": ("ready" if paper_state["session_count"] else "not_started"),
                    "last_paper_session_at": paper_state["last_session_at"],
                    "paper_session_count": paper_state["session_count"],
                },
            )

        if path == "/v1/paper/status":
            return respond(
                start_response, HTTPStatus.OK, request_id,
                data={
                    "mode": "paper",
                    "live_trading_enabled": False,
                    "session_state": "ready" if paper_state["session_count"] else "not_started",
                    "session_count": paper_state["session_count"],
                    "last_session_at": paper_state["last_session_at"],
                    "idempotency_storage": "sqlite_durable_lab_only",
                },
            )

        if path == "/v1/paper/session":
            if os.environ.get("RONINTRBOT_LAB_MODE", "").strip().lower() != "true":
                return error_response(
                    start_response, request_id, HTTPStatus.FORBIDDEN,
                    "paper_lab_only", "Paper session replay is disabled outside explicit lab mode.",
                )
            idempotency_key = environ.get("HTTP_IDEMPOTENCY_KEY", "").strip()
            if not 8 <= len(idempotency_key) <= 128:
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "idempotency_key_required", "Provide an Idempotency-Key header (8-128 characters).",
                )
            try:
                content_length = int(environ.get("CONTENT_LENGTH") or "0")
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > 16384:
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_paper_request", "Request body must be valid JSON and no larger than 16 KiB.",
                )
            try:
                raw_body = environ["wsgi.input"].read(content_length)
                payload = json.loads(raw_body.decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_paper_request", "Request body must be valid JSON.",
                )
            if not isinstance(payload, dict) or set(payload) != {"policy", "candles_ref"}:
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_paper_request", "Provide exactly policy and candles_ref.",
                )
            if payload.get("policy") != "trend":
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "unsupported_paper_policy", "Only the trend policy is available in this paper API slice.",
                )
            ref = payload.get("candles_ref")
            match = re.fullmatch(r"lbank:([a-z0-9]+_[a-z0-9]+):(15m|1h|4h):(60|[6-9][0-9]|1[0-9][0-9]|200)", ref or "")
            if not match:
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_candles_ref", "candles_ref must be lbank:<symbol>:<interval>:<60-200>.",
                )
            request_fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            try:
                reservation, cached_result = paper_store.get_or_reserve(idempotency_key, request_fingerprint)
            except Exception:
                return error_response(
                    start_response, request_id, HTTPStatus.SERVICE_UNAVAILABLE,
                    "paper_storage_unavailable", "Paper idempotency storage is temporarily unavailable.",
                )
            if reservation == "conflict":
                return error_response(
                    start_response, request_id, HTTPStatus.CONFLICT,
                    "idempotency_conflict", "This Idempotency-Key was already used for a different request.",
                )
            if reservation == "replay":
                return respond(start_response, HTTPStatus.OK, request_id, data=cached_result)
            if reservation == "in_progress":
                return error_response(
                    start_response, request_id, HTTPStatus.CONFLICT,
                    "paper_session_in_progress", "A request with this Idempotency-Key is already in progress.",
                )
            symbol, interval, limit = match.group(1), match.group(2), int(match.group(3))
            try:
                raw_candles = lbank_client.fetch_ohlcv(symbol, interval, limit)
                candles = tuple(StrategyCandle(
                    candle.timestamp, candle.open, candle.high, candle.low, candle.close, candle.volume
                ) for candle in raw_candles)
                policy = TrendPolicy(
                    timeframe=interval, market_type="spot", symbol=symbol,
                    limits=RiskLimits(max_notional_fraction=Decimal("0.95")),
                )
                engine = PaperEngine(PaperConfig())
                report = run_paper_session(candles, policy=policy, engine=engine)
            except MarketDataError:
                paper_store.release(idempotency_key, request_fingerprint)
                return error_response(
                    start_response, request_id, HTTPStatus.BAD_GATEWAY,
                    "market_data_unavailable", "LBank market data is temporarily unavailable or invalid.",
                )
            except ValueError:
                paper_store.release(idempotency_key, request_fingerprint)
                return error_response(
                    start_response, request_id, HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_paper_dataset", "The selected market history is insufficient or invalid.",
                )
            except Exception:
                try:
                    paper_store.release(idempotency_key, request_fingerprint)
                except Exception:
                    pass
                return error_response(
                    start_response, request_id, HTTPStatus.BAD_GATEWAY,
                    "paper_session_failed", "The paper simulation failed unexpectedly; no result was recorded.",
                )
            result = {
                "mode": "paper",
                "live_trading_enabled": False,
                "source": "lbank_public_spot",
                "symbol": symbol,
                "interval": interval,
                "candles_processed": report.candles_processed,
                "initial_equity": str(report.initial_equity),
                "final_equity": str(report.final_equity),
                "net_pnl": str(report.net_pnl),
                "total_fees": str(report.total_fees),
                "trade_count": report.trade_count,
                "halted": report.halted,
                "decisions": [
                    {"index": d.index, "timestamp": d.candle_timestamp, "action": d.action, "reason": d.reason}
                    for d in report.decisions[-20:]
                ],
                "notes": list(report.notes),
                "idempotency_storage": "sqlite_durable_lab_only",
            }
            try:
                paper_store.complete(idempotency_key, request_fingerprint, result)
            except Exception:
                try:
                    paper_store.release(idempotency_key, request_fingerprint)
                except Exception:
                    pass
                return error_response(
                    start_response, request_id, HTTPStatus.SERVICE_UNAVAILABLE,
                    "paper_storage_unavailable", "Paper result could not be persisted; retry with the same key.",
                )
            paper_state["session_count"] += 1
            paper_state["last_session_at"] = int(time.time())
            market_state["last_fetch_at"] = paper_state["last_session_at"]
            return respond(start_response, HTTPStatus.OK, request_id, data=result)

        if path == "/v1/strategy/analysis":
            query = parse_qs(
                environ.get("QUERY_STRING", ""), keep_blank_values=True
            )
            symbols = query.get("symbol", [])
            intervals = query.get("interval", [])
            if (
                len(symbols) != 1
                or not _MARKET_PATH.fullmatch(f"/v1/market/{symbols[0]}/ohlcv")
                or len(intervals) != 1
                or intervals[0] not in _ALLOWED_INTERVALS
            ):
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_strategy_request",
                    "Provide one valid symbol and one supported interval (15m, 1h, 4h).",
                )
            symbol, interval = symbols[0], intervals[0]
            try:
                raw_candles = lbank_client.fetch_ohlcv(symbol, interval, 200)
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
                    "invalid_strategy_request",
                    "The symbol or interval is invalid.",
                )

            candles = tuple(
                StrategyCandle(
                    candle.timestamp, candle.open, candle.high, candle.low,
                    candle.close, candle.volume,
                )
                for candle in raw_candles
            )
            try:
                analysis = analyze_timeframe(interval, candles)
            except ValueError as exc:
                if "not enough candles" in str(exc):
                    return error_response(
                        start_response,
                        request_id,
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "insufficient_market_history",
                        "At least 51 valid candles are required for strategy analysis.",
                    )
                return error_response(
                    start_response,
                    request_id,
                    HTTPStatus.BAD_GATEWAY,
                    "market_data_unavailable",
                    "LBank market data failed strategy validation.",
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
                    "direction": analysis.direction,
                    "score": analysis.score,
                    "threshold": SIGNAL_THRESHOLD,
                    "eligible": analysis.score >= SIGNAL_THRESHOLD,
                    "close": str(analysis.close),
                    "sma_fast": str(analysis.sma_fast),
                    "sma_slow": str(analysis.sma_slow),
                    "rsi": str(analysis.rsi),
                    "atr": str(analysis.atr),
                    "reasons": list(analysis.reasons),
                    "execution": "none",
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
