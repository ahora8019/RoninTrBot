from pathlib import Path
import json
import re


def test_worker_deploy_preserves_dashboard_managed_role_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    assert 'CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}' in workflow
    assert 'CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}' in workflow
    assert 'paths: ["workers/**", ".github/workflows/deploy-worker.yml"]' in workflow
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


def test_backup_docs_do_not_trigger_worker_deployment() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
    assert 'branches: [main]' in workflow
    assert 'paths: ["workers/**", ".github/workflows/deploy-worker.yml"]' in workflow
