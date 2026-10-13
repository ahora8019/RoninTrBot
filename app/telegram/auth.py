"""Role resolution and server-side action authorization."""

from __future__ import annotations

from collections.abc import Iterable

OWNER = "OWNER"
ADMIN = "ADMIN"
MEMBER = "MEMBER"

PUBLIC_ACTIONS = frozenset({"home", "help", "status", "paper", "trades", "strategy"})
ADMIN_ACTIONS = frozenset({"settings", "management"})


def resolve_role(user_id: int | None, owner_ids: Iterable[int], admin_ids: Iterable[int]) -> str:
    """Resolve a Telegram numeric user ID to the least-privileged applicable role."""
    if user_id is None:
        return MEMBER
    if user_id in set(owner_ids):
        return OWNER
    if user_id in set(admin_ids):
        return ADMIN
    return MEMBER


def action_allowed(action: str, role: str) -> bool:
    """Fail closed: unknown actions and unknown roles are denied."""
    if role not in {OWNER, ADMIN, MEMBER}:
        return False
    if action in PUBLIC_ACTIONS:
        return True
    if action in ADMIN_ACTIONS:
        return role in {OWNER, ADMIN}
    return False
