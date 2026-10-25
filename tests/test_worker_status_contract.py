from pathlib import Path


def test_system_status_reports_actual_users_kv_binding():
    source = (Path(__file__).resolve().parents[1] / "workers" / "ronin-panel" / "worker.js").read_text()
    assert 'function systemStatus(role, env)' in source
    assert '${env.USERS_KV ? "READY" : "MISSING"}' in source
    assert '"USERS_KV" in {}' not in source


def test_status_call_sites_pass_worker_environment():
    source = (Path(__file__).resolve().parents[1] / "workers" / "ronin-panel" / "worker.js").read_text()
    assert source.count("systemStatus(role, env)") >= 3
