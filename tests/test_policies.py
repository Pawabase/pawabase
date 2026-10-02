import pytest

from pawabase_core.policies import (
    Policy,
    PolicyEngine,
    PolicyError,
    clear_python_policies,
    evaluate,
    partial,
    policy,
    pushdown,
    python_policies,
    validate_condition,
)


def ctx(user_id="7", roles=(), perms=(), authenticated=True, service=False, record=None, **extra):
    return {
        "auth": {
            "authenticated": authenticated,
            "user_id": user_id if authenticated else None,
            "roles": list(roles),
            "permissions": list(perms),
            "kind": "user" if authenticated else "anonymous",
        },
        "credential": {
            "is_service": service,
            "role": "service" if service else "anon",
            "scopes": [],
        },
        "record": record,
        "project": "acme",
        "env": "dev",
        **extra,
    }


def test_shorthands_and_operators():
    assert evaluate({"authenticated": True}, ctx())
    assert not evaluate({"authenticated": True}, ctx(authenticated=False))
    assert evaluate({"role": "admin"}, ctx(roles=["admin"]))
    assert evaluate({"role": ["editor", "admin"]}, ctx(roles=["editor"]))
    assert not evaluate({"permission": "posts.write"}, ctx(perms=["posts.read"]))
    assert evaluate({"permission": "posts.write"}, ctx(perms=["*"]))
    assert evaluate({"owner": "author_id"}, ctx(record={"author_id": 7}))  # int vs str id
    assert not evaluate({"owner": "author_id"}, ctx(record={"author_id": 8}))
    assert evaluate(
        {"in": ["$record.status", ["draft", "review"]]}, ctx(record={"status": "draft"})
    )
    assert evaluate(
        {"matches": ["$record.email", "@example\\.com$"]}, ctx(record={"email": "a@example.com"})
    )
    assert evaluate({"not": {"exists": "$record.deleted_at"}}, ctx(record={"deleted_at": None}))
    assert evaluate({"eq": ["$$literal", "$project"]}, {"project": "$literal"})


def test_validation_rejects_bad_conditions():
    with pytest.raises(PolicyError):
        validate_condition({"bogus": 1})
    with pytest.raises(PolicyError):
        validate_condition({"eq": ["only one"]})
    with pytest.raises(PolicyError):
        validate_condition({"all": {"not": "a list"}})
    with pytest.raises(PolicyError):
        validate_condition({"matches": ["$a", "("]})


async def test_engine_check_and_service_bypass():
    engine = PolicyEngine({"editors": Policy("editors", {"role": "editor"})})
    assert (await engine.check("editors", ctx(roles=["editor"]))).allowed
    denied = await engine.check("editors", ctx())
    assert not denied and denied.policy == "editors"
    assert (await engine.check("deny", ctx(service=True))).allowed
    assert (await engine.check("role:admin", ctx(roles=["admin"]))).allowed
    assert (await engine.check(["authenticated", "permission:x"], ctx(perms=["x"]))).allowed
    with pytest.raises(PolicyError):
        engine.resolve_ref("nope")


async def test_python_policies():
    clear_python_policies()

    @policy("even-user")
    async def even_user(context):
        return int(context["auth"]["user_id"]) % 2 == 0

    engine = PolicyEngine(python=python_policies())
    assert (await engine.check("even-user", ctx(user_id="2"))).allowed
    assert not (await engine.check("even-user", ctx(user_id="3"))).allowed
    plan = engine.plan("even-user", ctx(user_id="2"))
    assert plan.allowed and plan.residual is not None
    assert await engine.row_allowed(plan, ctx(user_id="2", record={}))
    clear_python_policies()


def test_partial_and_pushdown_for_lists():
    engine = PolicyEngine()
    # Anonymous callers cannot see owned rows at all: refused before querying.
    assert engine.plan("owner", ctx(authenticated=False)).allowed is False
    plan = engine.plan("owner", ctx(user_id="42"))
    assert plan.allowed and plan.filters == {"owner_id": "42"} and plan.residual is None

    condition = {"any": [{"eq": ["$record.visibility", "public"]}, {"owner": "owner_id"}]}
    residual = partial(condition, ctx(user_id="5"))
    filters, rest = pushdown(residual)
    assert filters == {} and rest is not None  # disjunctions are checked per row
    assert evaluate(rest, {"record": {"visibility": "public", "owner_id": 1}})
    assert not evaluate(rest, {"record": {"visibility": "private", "owner_id": 1}})

    tenancy = {"all": [{"eq": ["$record.org_id", "$auth.org"]}, {"eq": ["$record.status", "live"]}]}
    plan = PolicyEngine().plan(tenancy, {**ctx(), "auth": {**ctx()["auth"], "org": "o1"}})
    assert plan.filters == {"org_id": "o1", "status": "live"}


def test_partial_pins_dollar_values_safely():
    context = ctx()
    context["auth"]["user_id"] = "$weird"
    residual = partial({"eq": ["$record.owner_id", "$auth.user_id"]}, context)
    filters, _ = pushdown(residual)
    assert filters == {"owner_id": "$weird"}


def test_input_is_deferred_at_plan_time_and_never_pushed_down():
    condition = {"all": [{"authenticated": True}, {"eq": ["$input.store_id", "$auth.org"]}]}
    context = {**ctx(), "auth": {**ctx()["auth"], "org": "shop"}}
    plan = PolicyEngine().plan(condition, context)  # no body yet: not refused
    assert plan.allowed and plan.filters == {} and plan.residual is not None
    assert evaluate(condition, {**context, "input": {"store_id": "shop"}})
    assert not evaluate(condition, {**context, "input": {"store_id": "other"}})
