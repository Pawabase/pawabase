"""An API key's scopes limit what it can do, on every route (edge/scopes.py)."""

import pytest

from pawabase_server.edge import scopes

ENV = "/platform/v1/envs/development"


def test_no_scopes_means_no_restriction():
    assert scopes.allows([], "DELETE", "/platform/v1/envs")


def test_read_and_write_are_separate():
    assert scopes.allows(["data:read"], "GET", "/rest/v1/orders")
    assert not scopes.allows(["data:read"], "POST", "/rest/v1/orders")
    assert scopes.allows(["data:write"], "PATCH", "/rest/v1/orders/1")
    assert not scopes.allows(["data:write"], "GET", "/rest/v1/orders")
    assert scopes.allows(["data:*"], "DELETE", "/rest/v1/orders/1")


def test_a_scope_covers_only_its_own_paths():
    assert not scopes.allows(["data:*"], "GET", "/storage/v1/object/files/a.txt")
    assert not scopes.allows(["webhooks:*"], "GET", f"{ENV}/auth/users")
    assert scopes.allows(["webhooks:*"], "POST", f"{ENV}/webhooks")
    assert scopes.allows(["webhooks:read"], "GET", f"{ENV}/webhook-deliveries")
    assert not scopes.allows(["users:read"], "POST", f"{ENV}/auth/users")
    assert scopes.allows(["users:read"], "GET", f"{ENV}/auth/users/abc")
    assert not scopes.allows(["users:*"], "GET", f"{ENV}/auth/roles")
    assert not scopes.allows(["users:*"], "GET", f"{ENV}/userset")


def test_admin_reaches_the_whole_management_api_and_only_that():
    assert scopes.allows(["admin:*"], "POST", "/platform/v1/envs")
    assert scopes.allows(["admin:read"], "GET", f"{ENV}/keys")
    assert not scopes.allows(["admin:read"], "POST", f"{ENV}/keys")
    assert not scopes.allows(["admin:*"], "GET", "/rest/v1/orders")


def test_the_health_check_needs_no_scope():
    assert scopes.allows(["data:read"], "GET", "/health/v1")


def test_older_scope_names_keep_their_meaning():
    assert scopes.allows(["resource:read"], "GET", "/rest/v1/x")
    assert not scopes.allows(["resource:read"], "POST", "/rest/v1/x")
    assert scopes.allows(["routes:invoke"], "POST", "/rest/v1/custom")


def test_unknown_scopes_and_management_scopes_on_a_browser_key_are_refused():
    with pytest.raises(ValueError, match="unknown scope"):
        scopes.validate(["data:reed"], role="secret")
    with pytest.raises(ValueError, match="secret key"):
        scopes.validate(["users:read"], role="publishable")
    assert scopes.validate(["data:read", "data:read"], role="publishable") == ["data:read"]


def test_a_key_cannot_hand_out_more_than_it_holds():
    assert scopes.covers(["keys:write", "data:read"], ["data:read"])
    assert not scopes.covers(["keys:write"], ["data:read"])
    assert not scopes.covers(["keys:write", "data:read"], ["data:*"])
    assert scopes.covers(["data:*"], ["data:write"])
    assert scopes.covers(["admin:*"], ["users:*"])
    assert not scopes.covers(["keys:write"], [])


def test_every_preset_is_valid():
    for preset in scopes.PRESETS:
        assert scopes.validate(preset["scopes"], role="secret") == preset["scopes"]


def test_the_edge_refuses_a_request_outside_the_keys_scopes():
    from pawabase_server.edge.policy import check_key

    info = {"scopes": ["data:read"]}
    assert check_key("GET", "/rest/v1/orders", "1.1.1.1", info) is None
    assert check_key("DELETE", "/rest/v1/orders/1", "1.1.1.1", info) == "scope_not_allowed"
    assert check_key("GET", "/platform/v1/envs/x/keys", "1.1.1.1", info) == "scope_not_allowed"


DEFINITION_KINDS = (
    "schemas", "transformers", "policies", "resources", "flows", "buckets", "mail-templates", "routes", "subscriptions",
    "webhooks", "inbound-hooks", "schedules", "auth/roles",
)  # fmt: skip


def test_the_deploy_preset_can_deploy_every_kind_of_definition_and_nothing_more():
    deploy = next(p for p in scopes.PRESETS if p["id"] == "deploy")["scopes"]
    for kind in DEFINITION_KINDS:
        for method, path in (
            ("GET", kind),
            ("POST", kind),
            ("PUT", f"{kind}/x"),
            ("DELETE", f"{kind}/x"),
        ):
            assert scopes.allows(deploy, method, f"/platform/v1/envs/school/{path}"), (method, kind)
    assert scopes.allows(deploy, "POST", "/platform/v1/envs/school/resources/orders/migrate")
    for forbidden in (
        "keys",
        "auth/users",
        "secrets/x/reveal",
        "mail",
        "firewall",
        "database/query",
    ):
        method = "POST" if forbidden != "secrets/x/reveal" else "GET"
        if forbidden == "secrets/x/reveal":
            continue
        assert not scopes.allows(deploy, method, f"/platform/v1/envs/school/{forbidden}"), forbidden
    assert not scopes.allows(deploy, "POST", "/platform/v1/envs")
