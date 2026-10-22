from pathlib import Path
import json
import re


def test_python_worker_fails_closed_until_routes_are_migrated():
    source = Path("workers/ronin-core-python/entry.py").read_text(encoding="utf-8")
    assert "INTERNAL_API_SECRET" in source
    assert "hmac.compare_digest" in source
    assert '"route_not_ready"' in source
    assert '"live_trading_enabled": False' in source
    assert "urlsplit(request.url)" in source


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
