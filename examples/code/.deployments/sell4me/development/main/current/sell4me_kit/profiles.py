"""What Sell4me kept on the user (identity itself is Akountz's): name, avatar, timezone, last store, the signup survey."""

from __future__ import annotations

from typing import Any

from . import q
from .context import Ctx

HEARD_FROM = {"search", "social", "friend", "ad", "press", "other"}
SIGNUP_GOALS = {"physical", "digital", "services", "not_sure"}


async def ensure_profile(c: Ctx, **survey: Any) -> q.Row:
    """The caller's profile, created on first use from their Akountz identity."""
    user_id = c.require_user()
    db = await c.db()
    profile = await q.first(db, "profiles", {"user_id": user_id})
    claims = (c.auth.get("claims") or {}).get("meta") or {}
    if profile is None:
        heard = survey.get("heard_from") or claims.get("heard_from")
        goal = survey.get("signup_goal") or claims.get("signup_goal")
        profile = await q.insert(db, "profiles", {
            "user_id": user_id, "email": c.email, "full_name": survey.get("full_name") or claims.get("full_name") or claims.get("name"),
            "heard_from": heard if heard in HEARD_FROM else None, "signup_goal": goal if goal in SIGNUP_GOALS else None, "timezone_name": "UTC"})
    return profile


def profile_prop(profile: q.Row) -> dict[str, Any]:
    name = profile.full_name or (profile.email or "").split("@")[0]
    return {"id": profile.user_id, "email": profile.email, "name": name, "full_name": profile.full_name, "avatar_url": profile.avatar_url,
            "timezone_name": profile.timezone_name, "last_store_id": profile.last_store_id, "heard_from": profile.heard_from, "signup_goal": profile.signup_goal}
