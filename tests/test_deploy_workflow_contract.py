from pathlib import Path


def test_worker_deploy_preserves_dashboard_managed_role_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")

    assert 'CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}' in workflow
    assert 'CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}' in workflow
    assert 'paths: ["workers/**"]' in workflow
    assert 'paths: ["workers/**", ".github/workflows/deploy-worker.yml"]' not in workflow
    assert "--keep-vars" in workflow, (
        "Worker deploy must preserve dashboard-managed OWNER_TELEGRAM_ID and "
        "ADMIN_TELEGRAM_IDS variables; wrangler.jsonc intentionally does not "
        "contain personal Telegram IDs."
    )


def test_deploy_secret_presence_check_uses_environment_variables() -> None:
    workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")

    assert 'if [ -z "$CHECK_API_TOKEN" ]' in workflow
    assert '[ -z "$CHECK_ACCOUNT_ID" ]' in workflow
