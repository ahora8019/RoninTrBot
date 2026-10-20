#!/usr/bin/env python3
"""Read-only post-deployment smoke test for RoninTrBot's Worker edge.

Checks health and proves that an unauthenticated synthetic POST is rejected.
It does not call Telegram or perform trading actions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

REQUIRED_FIELDS = {"service", "version", "gitSha", "status", "engine", "paperTrading", "webhookAuth"}
USER_AGENT = "RoninTrBot-SmokeTest/1.0 (+python)"
DEFAULT_BASE_URL = "https://ronintrbot.ahora-project.workers.dev"


def smoke(base_url: str, timeout: float = 10.0) -> list[str]:
    base = base_url.rstrip("/")
    try:
        request = urllib.request.Request(base + "/", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status != 200:
                return [f"health GET returned {resp.status}, expected 200"]
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return ["health GET returned 403 — check Cloudflare edge bot-protection rules"]
        return [f"health GET failed: HTTP {exc.code}"]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [f"health GET failed: {type(exc).__name__}: {exc}"]

    if not isinstance(payload, dict):
        return ["health response must be a JSON object"]

    failures: list[str] = []
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        failures.append(f"health payload missing fields: {sorted(missing)}")
    if payload.get("status") != "online":
        failures.append(f"status is {payload.get('status')!r}, expected 'online'")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(payload.get("version", ""))):
        failures.append(f"version {payload.get('version')!r} is not semver")
    if payload.get("webhookAuth") != "configured":
        failures.append(
            f"webhookAuth is {payload.get('webhookAuth')!r}; expected 'configured'"
        )
    if failures:
        return failures

    # A health flag alone cannot prove that the Worker enforces the secret.
    # Send an empty synthetic update without the Telegram secret header and
    # require HTTP 403. The Worker must reject it before parsing/processing it.
    probe = urllib.request.Request(
        base + "/",
        data=b"{}",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        return [f"webhook auth probe failed: {type(exc).__name__}: {exc}"]

    if status != 403:
        failures.append(
            f"unauthenticated webhook POST returned HTTP {status}; expected 403"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
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
