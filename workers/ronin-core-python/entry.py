"""Cloudflare Python Worker entrypoint for RoninTrBot Core.

Only public LBank market data is enabled here. Strategy and paper-session routes
remain fail-closed until their orchestration/runtime tests pass.
"""
from __future__ import annotations

import hmac
import json
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from workers import Response, WorkerEntrypoint

_MARKET_PATH = re.compile(r"^/v1/market/([a-z0-9]+_[a-z0-9]+)/ohlcv$")
_ALLOWED_INTERVALS = {"15m", "1h", "4h"}
_MAX_MARKET_LIMIT = 500


def _json_response(status: int, payload: dict, request_id: str) -> Response:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return Response.new(body, status=status, headers={
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-request-id": request_id,
    })


def _valid_bearer(authorization: str, secret: str) -> bool:
    """Validate a bearer token without crashing on non-ASCII attacker input."""
    scheme, separator, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8"))


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        request_id = str(uuid4())
        method = request.method.upper()
        parsed_url = urlsplit(request.url)
        path = parsed_url.path.rstrip("/") or "/"
        secret = getattr(self.env, "INTERNAL_API_SECRET", "") or ""
        if not secret.strip():
            return _json_response(503, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "service_not_configured",
                          "message": "Internal API authentication is not configured."}
            }, request_id)

        authorization = request.headers.get("authorization", "")
        if not _valid_bearer(authorization, secret):
            return _json_response(401, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "unauthorized", "message": "A valid bearer token is required."}
            }, request_id)

        if method not in ("GET", "POST"):
            return _json_response(405, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "method_not_allowed", "message": "Only GET and POST are supported."}
            }, request_id)

        if path == "/v1/health" and method == "GET":
            return _json_response(200, {
                "ok": True, "request_id": request_id,
                "data": {
                    "status": "ok",
                    "version": getattr(self.env, "RONINTRBOT_VERSION", "0.1.0"),
                    "commit": getattr(self.env, "GIT_SHA", "unknown"),
                    "stale_after_seconds": 60,
                },
                "error": None
            }, request_id)

        if path in ("/v1/status", "/v1/paper/status") and method == "GET":
            data = {
                "service": "ronintrbot-python-core",
                "trading_mode": "paper",
                "live_trading_enabled": False,
                "engine_state": "not_started",
                "market_data_state": "not_connected",
                "paper_engine_state": "not_started",
                "paper_session_count": 0,
                "runtime": "cloudflare-python-worker",
            }
            if path == "/v1/paper/status":
                data = {
                    "mode": "paper", "live_trading_enabled": False,
                    "session_state": "not_started", "session_count": 0,
                    "idempotency_storage": "d1_not_enabled",
                }
            return _json_response(200, {
                "ok": True, "request_id": request_id, "data": data, "error": None
            }, request_id)

        market_match = _MARKET_PATH.fullmatch(path)
        if market_match and method == "GET":
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            intervals = query.get("interval", [])
            limits = query.get("limit", ["200"])
            if (len(intervals) != 1 or intervals[0] not in _ALLOWED_INTERVALS
                    or len(limits) != 1 or not re.fullmatch(r"[0-9]{1,3}", limits[0])):
                return _json_response(422, {
                    "ok": False, "request_id": request_id, "data": None,
                    "error": {"code": "invalid_market_request",
                              "message": "Provide one interval (15m, 1h, 4h) and limit (1-500)."}
                }, request_id)
            limit = int(limits[0])
            if not 1 <= limit <= _MAX_MARKET_LIMIT:
                return _json_response(422, {
                    "ok": False, "request_id": request_id, "data": None,
                    "error": {"code": "invalid_market_request",
                              "message": "limit must be between 1 and 500."}
                }, request_id)
            try:
                from js import fetch
                from app.market_data.lbank_async import (
                    AsyncLBankMarketDataClient, AsyncMarketDataError,
                )
                client = AsyncLBankMarketDataClient(fetch)
                candles = await client.fetch_ohlcv(market_match.group(1), intervals[0], limit)
            except ValueError:
                return _json_response(422, {
                    "ok": False, "request_id": request_id, "data": None,
                    "error": {"code": "invalid_market_request",
                              "message": "The symbol, interval, or limit is invalid."}
                }, request_id)
            except Exception as exc:
                # Do not expose upstream details or internal runtime data to callers.
                try:
                    from app.market_data.lbank_async import AsyncMarketDataError
                    if not isinstance(exc, AsyncMarketDataError):
                        pass
                except Exception:
                    pass
                return _json_response(502, {
                    "ok": False, "request_id": request_id, "data": None,
                    "error": {"code": "market_data_unavailable",
                              "message": "LBank market data is temporarily unavailable or invalid."}
                }, request_id)
            return _json_response(200, {
                "ok": True, "request_id": request_id,
                "data": {
                    "source": "lbank_public_spot",
                    "market_type": "spot",
                    "symbol": market_match.group(1),
                    "interval": intervals[0],
                    "count": len(candles),
                    "candles": [{
                        "timestamp": candle.timestamp,
                        "open": str(candle.open), "high": str(candle.high),
                        "low": str(candle.low), "close": str(candle.close),
                        "volume": str(candle.volume),
                    } for candle in candles],
                    "execution": "none",
                },
                "error": None,
            }, request_id)

        # Strategy and session routes stay unavailable until their orchestration
        # and D1 idempotency have passed dedicated Cloudflare runtime tests.
        if path == "/v1/strategy/analysis" or path == "/v1/paper/session":
            return _json_response(503, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "route_not_ready",
                          "message": "This route is not enabled until its Cloudflare runtime tests pass."}
            }, request_id)

        return _json_response(404, {
            "ok": False, "request_id": request_id, "data": None,
            "error": {"code": "not_found", "message": "Route not found."}
        }, request_id)
