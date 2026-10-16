from pathlib import Path


def test_worker_deploy_preserves_dashboard_managed_role_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")

    assert "--keep-vars" in workflow, (
        "Worker deploy must preserve dashboard-managed OWNER_TELEGRAM_ID and "
        "ADMIN_TELEGRAM_IDS variables; wrangler.jsonc intentionally does not "
        "contain personal Telegram IDs."
    )


def test_deploy_secret_presence_check_uses_environment_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")

    assert 'if [ -z "$CHECK_API_TOKEN" ]' in workflow
    assert 'if [ -z "$CHECK_ACCOUNT_ID" ]' in workflow
