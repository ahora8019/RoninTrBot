"""Small, dependency-free WSGI surface for the internal Python-core API.

The first slice intentionally exposes only health and read-only status. It does
not start a paper session, fetch market data, or expose exchange-order routes.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from http import HTTPStatus
from typing import Callable
from uuid import uuid4
from wsgiref.simple_server import make_server


_JSON_HEADERS = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
]
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _build_metadata(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


def create_app(
    *,
    secret: str | None = None,
    version: str | None = None,
    commit: str | None = None,
) -> Callable:
    """Create a WSGI application; inject configuration in tests, env in runtime."""
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

    def application(environ, start_response):
        request_id = str(uuid4())
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        if not configured_secret:
            return respond(
                start_response,
                HTTPStatus.SERVICE_UNAVAILABLE,
                request_id,
                error={
                    "code": "service_not_configured",
                    "message": "Internal API authentication is not configured.",
                },
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
                    "market_data_state": "not_connected",
                    "paper_engine_state": "not_started",
                },
            )

        return respond(
            start_response,
            HTTPStatus.NOT_FOUND,
            request_id,
            error={
                "code": "not_found",
                "message": "The requested endpoint is not available in this API version.",
            },
        )

    return application


application = create_app()


if __name__ == "__main__":
    # Local development only. Production must terminate TLS at a trusted host/proxy.
    with make_server(
        "127.0.0.1", int(os.environ.get("PORT", "8080")), application
    ) as server:
        server.serve_forever()
