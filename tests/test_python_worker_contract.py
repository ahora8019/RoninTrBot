from pathlib import Path
import json
import re


def test_python_worker_fails_closed_until_routes_are_migrated():
    source = Path("workers/ronin-core-python/entry.py").read_text(encoding="utf-8")
    assert 'INTERNAL_API_SECRET' in source
    assert 'hmac.compare_digest' in source
    assert '"route_not_ready"' in source
    assert '"live_trading_enabled": False' in source
    assert 'urlsplit(request.url)' in source


def test_python_worker_config_has_python_runtime_and_d1_binding():
    raw = Path("workers/ronin-core-python/wrangler.jsonc").read_text(encoding="utf-8")
    config = json.loads(re.sub(r"/\*.*?\*/|^\s*//.*$", "", raw, flags=re.S | re.M))
    assert config["main"] == "entry.py"
    assert "python_workers" in config["compatibility_flags"]
    assert config["d1_databases"][0]["binding"] == "PAPER_DB"
    assert config["d1_databases"][0]["database_id"] == "REPLACE_WITH_PREVIEW_D1_DATABASE_ID"


def test_d1_schema_has_primary_key_and_constrained_state():
    schema = Path("workers/ronin-core-python/migrations/0001_paper_idempotency.sql").read_text(encoding="utf-8")
    assert "idempotency_key TEXT PRIMARY KEY" in schema
    assert "state IN ('in_progress', 'completed')" in schema
