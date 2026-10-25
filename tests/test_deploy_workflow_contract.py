from pathlib import Path
import json
import re


def test_worker_deploy_preserves_dashboard_managed_role_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    assert 'CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}' in workflow
    assert 'CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}' in workflow
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow.split("permissions:", 1)[0]
    assert "--keep-vars" in workflow


def test_deploy_secret_presence_check_uses_environment_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    assert 'if [ -z "$CHECK_API_TOKEN" ]' in workflow
    assert '[ -z "$CHECK_ACCOUNT_ID" ]' in workflow


def test_worker_deploy_target_matches_project_account_resources() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    config_text = Path("workers/ronin-panel/wrangler.jsonc").read_text(encoding="utf-8")
    config_text = re.sub(r"/\*.*?\*/", "", config_text, flags=re.S)
    config_text = re.sub(r"^\s*//.*$", "", config_text, flags=re.M)
    config = json.loads(config_text)

    assert config["name"] == "ronintrbot"
    assert config["kv_namespaces"][0]["binding"] == "USERS_KV"
    assert config["kv_namespaces"][0]["id"] == "97fdd4c0960e4b60bedee671172b52b7"
    assert "https://ronintrbot.ahora-project.workers.dev" in workflow


def test_merging_code_does_not_automatically_deploy_worker() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert "push:" not in trigger
    assert "pull_request:" not in trigger


def test_market_data_accepts_compact_and_underscored_symbols() -> None:
    worker = Path("workers/ronin-panel/worker.js").read_text(encoding="utf-8")
    assert "normalizeMarketSymbol(symbol)" in worker
    assert '["btc_usdt", "eth_usdt", "sol_usdt", "xrp_usdt", "bnb_usdt", "doge_usdt"]' in worker

def test_worker_rejects_telegram_updates_without_webhook_secret() -> None:
    worker = Path("workers/ronin-panel/worker.js").read_text(encoding="utf-8")
    assert 'if (!env.WEBHOOK_SECRET)' in worker
    assert 'return json({ error: "Webhook secret not configured" }, 503)' in worker
    assert 'return json({ error: "Forbidden" }, 403)' in worker


def test_deploy_secret_gate_only_runs_on_main() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    condition = "github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch'"
    assert f"secrets-present:\n    if: {condition}" in workflow
    assert f"deploy:\n    if: {condition}" in workflow
