"""Creating a scoped API key through the management API."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"


async def test_creating_keys_validates_scopes_and_lists_the_catalog(api):
    with pytest.raises(ServiceError) as error:
        await api.studio.post(f"{ENV}/keys", json={"name": "x", "role": "secret", "scopes": ["nope:read"]})
    assert error.value.status == 422
    made = await api.studio.post(
        f"{ENV}/keys", json={"name": "users", "role": "secret", "scopes": ["users:read"]}
    )
    assert made["scopes"] == ["users:read"]
    catalog = await api.studio.get("/platform/v1/key-scopes")
    assert {f["id"] for f in catalog["families"]} >= {"data", "users", "webhooks", "admin"}


#: Management routes only an admin key may reach: the environment itself, releases and branches, and anything that spans environments.
ADMIN_ONLY = (
    "/platform/v1/agent/", "/platform/v1/audit", "/platform/v1/blueprints", "/platform/v1/cache", "/platform/v1/capacity",
    "/platform/v1/code", "/platform/v1/envs", "/platform/v1/key-scopes", "/platform/v1/overview", "/platform/v1/retry-policies",
    "/platform/v1/runtime", "/platform/v1/usage", "/platform/v1/workers",
)  # fmt: skip
ENV_ADMIN_ONLY = ("api-versions", "branches", "deployments", "previews", "promote", "releases", "revisions", "auth/hosted-source")


async def test_every_management_route_is_covered_by_a_narrow_scope_or_deliberately_admin_only(api):
    """A new management route that no scope family names would be unreachable for every key but an admin's: decide, don't drift."""
    import contextlib
    import io
    import re

    from pawabase_server.edge import scopes

    listing = io.StringIO()
    with contextlib.redirect_stdout(listing):
        api.app.print_routes()
    forgotten = []
    for line in listing.getvalue().splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "MOUNT" or not parts[1].startswith("/platform/v1/"):
            continue
        path = parts[1]
        concrete = re.sub(r"\{[^}]+\}", "x", path.replace("{env}", "school"))
        for method in (m for m in parts[0].split(",") if m not in ("HEAD", "OPTIONS")):
            if any(scopes.allows([f"{f.id}:*"], method, concrete) for f in scopes.FAMILIES if f.id != "admin"):
                continue
            tail = path.removeprefix("/platform/v1/envs/{env}/")
            deliberate = path.startswith(ADMIN_ONLY) and not tail.startswith(("auth/", "resources")) and path != "/platform/v1/envs/{env}/auth"
            deliberate = deliberate or path in ("/platform/v1/envs/{env}", "/platform/v1/envs/{env}/auth") or tail.startswith(ENV_ADMIN_ONLY)
            if not deliberate:
                forgotten.append((method, path))
    assert forgotten == [], f"add these to a scope family in edge/scopes.py, or to ADMIN_ONLY here: {forgotten}"
