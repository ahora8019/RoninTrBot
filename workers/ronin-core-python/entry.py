"""Cloudflare Python Worker entrypoint for RoninTrBot Core.

Routes not migrated to the async runtime fail closed; they never fall back to a
second strategy implementation or to a local filesystem database.
"""
from __future__ import annotations

import hmac
import json
from urllib.parse import urlsplit
from uuid import uuid4

from workers import Response, WorkerEntrypoint


def _json_response(status: int, payload: dict, request_id: str) -> Response:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return Response.new(body, status=status, headers={
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-request-id": request_id,
    })


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        request_id = str(uuid4())
        method = request.method.upper()
        parsed_url = urlsplit(request.url)
        path = parsed_url.path.rstrip("/") or "/"
        secret = getattr(self.env, "INTERNAL_API_SECRET", "") or ""
        if not secret:
            return _json_response(503, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "service_not_configured",
                          "message": "Internal API authentication is not configured."}
            }, request_id)

        authorization = request.headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied, secret):
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

        # Market, strategy, and session routes remain unavailable until their
        # async transport and D1 orchestration have passed runtime tests.
        if path.startswith("/v1/market/") or path in ("/v1/strategy/analysis", "/v1/paper/session"):
            return _json_response(503, {
                "ok": False, "request_id": request_id, "data": None,
                "error": {"code": "route_not_ready",
                          "message": "This route is not enabled until its Cloudflare runtime tests pass."}
            }, request_id)

        return _json_response(404, {
            "ok": False, "request_id": request_id, "data": None,
            "error": {"code": "not_found", "message": "Route not found."}
        }, request_id)
