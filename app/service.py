"""Higher-level orchestration helpers used by the API and the seed script."""
from __future__ import annotations

from app import repo


def bootstrap_group(name: str, member_names: list[str]) -> tuple[int, list[dict]]:
    """Create a group with its system agents (admin, search) and one person +
    person-agent per member. Returns (group_id, [{user_id, name, token}])."""
    group_id = repo.create_group(name)
    repo.create_agent(group_id, "admin", "Organisator")
    repo.create_agent(group_id, "search", "Rechercheur")

    members: list[dict] = []
    for display_name in member_names:
        user_id, token = repo.create_user(group_id, display_name)
        repo.create_agent(group_id, "person", f"{display_name}s Agent", user_id=user_id)
        members.append({"user_id": user_id, "name": display_name, "token": token})
    return group_id, members
