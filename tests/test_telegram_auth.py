from app.telegram.auth import ADMIN, MEMBER, OWNER, action_allowed, resolve_role


def test_role_resolution_prioritizes_owner():
    assert resolve_role(10, [10], [10, 20]) == OWNER


def test_role_resolution_admin_and_member():
    assert resolve_role(20, [10], [20]) == ADMIN
    assert resolve_role(30, [10], [20]) == MEMBER
    assert resolve_role(None, [10], [20]) == MEMBER


def test_public_actions_allowed_for_all_known_roles():
    for role in (OWNER, ADMIN, MEMBER):
        assert action_allowed("status", role)
        assert action_allowed("paper", role)
        assert action_allowed("market", role)


def test_management_actions_require_admin_or_owner():
    assert action_allowed("management", OWNER)
    assert action_allowed("management", ADMIN)
    assert not action_allowed("management", MEMBER)


def test_unknown_actions_and_roles_fail_closed():
    assert not action_allowed("start_engine", OWNER)
    assert not action_allowed("unknown", ADMIN)
    assert not action_allowed("settings", "UNKNOWN")
