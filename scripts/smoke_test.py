#!/usr/bin/env python3
"""RoninTrBot post-deployment smoke test (worker edge).

Verifies, with REAL requests: health endpoint shape, expected fields, version
format, and that the webhook-auth gate is active. Read-only; no Telegram calls,
no trading anything. Exits non-zero on any failure so CI can gate promotion.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

REQUIRED_FIELDS = {"service", "version", "status", "engine", "paperTrading", "webhookAuth"}
# NOTE (verified 2026-10-14): the Cloudflare edge bot-protection blocks the
# default "Python-urllib/x.y" user agent with HTTP 403 — the health endpoint
# itself is fine. Use an explicit descriptive UA.
USER_AGENT = "RoninTrBot-SmokeTest/1.0 (+python)"


def smoke(base_url: str, timeout: float = 10.0) -> list[str]:
    base = base_url.rstrip("/")
    failures: list[str] = []

    try:
        request = urllib.request.Request(base + "/", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status != 200:
                failures.append(f"health GET returned {resp.status}, expected 200")
                return failures
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return ["health GET returned 403 — if you changed the UA, check the edge "
                    "bot-protection block (UA heuristics), not the worker"]
        return [f"health GET failed: {exc}"]

    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        failures.append(f"health payload missing fields: {sorted(missing)}")
    if payload.get("status") != "online":
        failures.append(f"status is {payload.get('status')!r}, expected 'online'")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(payload.get("version", ""))):
        failures.append(f"version {payload.get('version')!r} is not semver")
    if payload.get("webhookAuth") not in ("secret_verified", "not_configured"):
        failures.append(f"webhookAuth {payload.get('webhookAuth')!r} is not a known state")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://ronin-panel.ronintrb.workers.dev")
    args = parser.parse_args()
    failures = smoke(args.base_url)
    for failure in failures:
        print(f"SMOKE FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"SMOKE PASS: {args.base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
