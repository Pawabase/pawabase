"""Indexes a resource declares: planned, applied, edited, dropped and rolled back like any other change to its table."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"
ORDERS = f"{ENV}/resources/orders"
FIELDS = [
    ("tenant", "string"),
    ("number", "integer"),
    ("status", "string"),
    ("email", "string"),
    ("deleted_at", "datetime"),
]


def definition(*indexes, fields=FIELDS):
    return {
        "name": "orders",
        "fields": [{"name": n, "type": t} for n, t in fields],
        "indexes": list(indexes),
        "operations": {"list": {"enabled": True, "policy": "public"}},
    }


RECENT = {"name": "by_tenant", "columns": ["tenant", {"name": "number", "desc": True}]}
UNIQUE_EMAIL = {"name": "one_email", "columns": ["email"], "unique": True}
OPEN = {
    "name": "open_only",
    "columns": ["status"],
    "where": [{"column": "deleted_at", "op": "is_null"}],
}


@pytest.fixture
async def orders(api):
    await api.studio.post(f"{ENV}/resources", json=definition())
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    return api


async def listed(api):
    return {i["name"]: i for i in (await api.studio.get(f"{ORDERS}/indexes"))["data"]}


async def sql(api, text):
    return await api.studio.post(f"{ENV}/database/query", json={"sql": text, "allow_write": True})


async def test_a_declared_index_is_pending_then_built_then_rolled_back(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT, OPEN))
    report = await api.studio.get(f"{ORDERS}/schema")
    assert report["state"] == "pending"
    assert sorted(c["id"] for c in report["changes"]) == [
        "add_index:index:by_tenant",
        "add_index:index:open_only",
    ]
    assert (await listed(api))["by_tenant"]["state"] == "missing"

    applied = await api.studio.post(f"{ORDERS}/schema/apply", json={})
    assert applied["applied"] and len(applied["statements"]) == 2
    indexes = await listed(api)
    assert indexes["by_tenant"]["origin"] == "declared" and indexes["by_tenant"]["state"] == "ok"
    assert indexes["by_tenant"]["columns"] == ["tenant", "number"]
    assert indexes["by_tenant"]["desc"] == ["number"]
    assert indexes["open_only"]["partial"] is True
    assert (await api.studio.get(f"{ORDERS}/schema"))["state"] == "in_sync"

    history = (await api.studio.get(f"{ORDERS}/migrations"))["data"]
    done = await api.studio.post(f"{ORDERS}/migrations/{history[0]['id']}/rollback")
    assert done["rolled_back"] == history[0]["id"]
    assert "by_tenant" not in {n for n, i in (await listed(api)).items() if i["state"] == "ok"}
    # The definition still declares them, so they are pending again.
    assert (await api.studio.get(f"{ORDERS}/schema"))["state"] == "pending"


async def test_a_unique_index_over_duplicates_is_refused_with_the_values(orders):
    api = orders
    for n in (1, 2):
        await api.studio.post(f"{ORDERS}/records", json={"email": "a@x.test", "number": n})
    await api.studio.put(ORDERS, json=definition(UNIQUE_EMAIL))
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{ORDERS}/schema/apply", json={})
    assert refused.value.status == 422
    assert "a@x.test" in str(refused.value) and "more than once" in str(refused.value)
    assert (await listed(api))["one_email"]["state"] == "missing"  # nothing was half-built

    records = (await api.studio.get(f"{ORDERS}/records"))["data"]
    await api.studio.delete(f"{ORDERS}/records/{records[0]['id']}")
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    assert (await listed(api))["one_email"]["unique"] is True
    # One of the two rows is still there, so the same address is now refused as a conflict, not a crash.
    with pytest.raises(ServiceError) as conflict:
        await api.studio.post(f"{ORDERS}/records", json={"email": "a@x.test"})
    assert conflict.value.status == 409


async def test_editing_an_index_swaps_it_and_a_rollback_brings_the_old_one_back(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT))
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    old = next(i["physical"] for i in (await listed(api)).values() if i["name"] == "by_tenant")

    edited = {**RECENT, "columns": ["tenant", "status"]}
    await api.studio.put(ORDERS, json=definition(edited))
    report = await api.studio.get(f"{ORDERS}/schema")
    assert [(c["op"], c["safe"]) for c in report["changes"]] == [("replace_index", False)]
    stale = await listed(api)
    assert stale["by_tenant"]["state"] == "ok" or stale["by_tenant"]["state"] in (
        "stale",
        "missing",
    )

    with pytest.raises(ServiceError):  # rebuilding needs confirming
        await api.studio.post(
            f"{ORDERS}/schema/apply", json={"changes": ["replace_index:index:by_tenant"]}
        )
    await api.studio.post(
        f"{ORDERS}/schema/apply",
        json={"changes": ["replace_index:index:by_tenant"], "confirm": "orders"},
    )
    now = await listed(api)
    assert now["by_tenant"]["columns"] == ["tenant", "status"] and now["by_tenant"]["state"] == "ok"
    assert old not in {i["physical"] for i in (await api.studio.get(f"{ORDERS}/indexes"))["data"]}

    history = (await api.studio.get(f"{ORDERS}/migrations"))["data"]
    await api.studio.post(f"{ORDERS}/migrations/{history[0]['id']}/rollback")
    names = {i["physical"] for i in (await api.studio.get(f"{ORDERS}/indexes"))["data"]}
    assert old in names


async def test_removing_a_declaration_drops_the_index_only_when_confirmed(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT))
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    await api.studio.put(ORDERS, json=definition())
    report = await api.studio.get(f"{ORDERS}/schema")
    assert [c["op"] for c in report["changes"]] == ["drop_index"] and report["state"] == "review"
    assert (await listed(api))["by_tenant"]["state"] == "stale"
    # A plain apply (the safe changes) leaves it alone.
    assert (await api.studio.post(f"{ORDERS}/schema/apply", json={}))["applied"] is False
    await api.studio.post(
        f"{ORDERS}/schema/apply",
        json={"changes": ["drop_index:index:by_tenant"], "confirm": "orders"},
    )
    assert "by_tenant" not in await listed(api)


async def test_a_hand_made_index_is_listed_left_alone_and_droppable_on_the_record(orders):
    api = orders
    await sql(api, "CREATE INDEX hand_made ON orders (status, number)")
    index = (await listed(api))["hand_made"]
    assert (
        index["origin"] == "external"
        and index["droppable"]
        and index["columns"] == ["status", "number"]
    )
    assert (await api.studio.get(f"{ORDERS}/schema"))[
        "state"
    ] == "in_sync"  # not Pawabase's to change

    with pytest.raises(ServiceError):  # the resource's name must be typed
        await api.studio.post(f"{ORDERS}/indexes/hand_made/drop", json={"confirm": "nope"})
    done = await api.studio.post(f"{ORDERS}/indexes/hand_made/drop", json={"confirm": "orders"})
    assert done["dropped"] == "hand_made" and "hand_made" not in await listed(api)

    history = (await api.studio.get(f"{ORDERS}/migrations"))["data"]
    assert history[0]["summary"] == "Drop index hand_made" and history[0]["can_roll_back"]
    await api.studio.post(f"{ORDERS}/migrations/{history[0]['id']}/rollback")
    assert "hand_made" in await listed(api)


async def test_keys_and_field_indexes_cannot_be_dropped_as_if_they_were_hand_made(orders):
    api = orders
    await api.studio.put(
        ORDERS,
        json={
            **definition(),
            "fields": [
                {"name": "tenant", "type": "string", "indexed": True},
                {"name": "number", "type": "integer"},
            ],
        },
    )
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    indexes = await listed(api)
    field_index = next(i for i in indexes.values() if i["origin"] == "field")
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(
            f"{ORDERS}/indexes/{field_index['physical']}/drop", json={"confirm": "orders"}
        )
    assert "change the field" in str(refused.value)
    with pytest.raises(ServiceError):
        await api.studio.post(f"{ORDERS}/indexes/not_there/drop", json={"confirm": "orders"})


@pytest.mark.parametrize(
    ("indexes", "message"),
    [
        ([{"name": "x", "columns": ["nope"]}], "not a column"),
        ([{"name": "x", "columns": []}], "at least one column"),
        ([{"name": "X!", "columns": ["tenant"]}], "name must be"),
        (
            [{"name": "x", "columns": ["tenant"]}, {"name": "x", "columns": ["status"]}],
            "appears twice",
        ),
        (
            [{"name": "a", "columns": ["tenant"]}, {"name": "b", "columns": ["tenant"]}],
            "duplicates",
        ),
        ([{"name": "x", "columns": ["tenant", "tenant"]}], "listed twice"),
        (
            [
                {
                    "name": "x",
                    "columns": ["tenant"],
                    "where": [{"column": "status", "op": "like", "value": "a"}],
                }
            ],
            "condition",
        ),
        ([{"name": "x", "columns": ["tenant"], "method": "gin"}], "method"),
        ([{"name": "x", "columns": ["tenant"], "method": "hash", "unique": True}], "unique"),
        (
            [{"name": f"i{n}", "columns": ["tenant", "status"][: 1 + n % 2]} for n in range(20)],
            "at most",
        ),
    ],
)
async def test_a_bad_declaration_is_refused_when_the_definition_is_saved(api, indexes, message):
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{ENV}/resources", json=definition(*indexes))
    assert refused.value.status == 422 and message in str(refused.value)


async def test_a_condition_value_can_never_become_sql(orders):
    api = orders
    nasty = {
        "name": "tricky",
        "columns": ["status"],
        "where": [{"column": "status", "op": "=", "value": "x'); DROP TABLE orders; --"}],
    }
    await api.studio.put(ORDERS, json=definition(nasty))
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    assert (await listed(api))["tricky"]["state"] == "ok"
    assert (await api.studio.get(f"{ORDERS}/records"))["data"] == []  # the table is still there


async def test_a_field_an_index_depends_on_cannot_be_removed_from_the_definition(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT))
    with pytest.raises(ServiceError) as refused:
        await api.studio.put(
            ORDERS, json=definition(RECENT, fields=[("tenant", "string"), ("status", "string")])
        )
    assert "not a column" in str(refused.value)


async def test_retyping_a_column_keeps_the_indexes_that_cover_it(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT))
    await api.studio.post(f"{ORDERS}/schema/apply", json={})
    await api.studio.post(f"{ORDERS}/records", json={"tenant": "a", "number": 5})
    retyped = [
        ("tenant", "string"),
        ("number", "number"),
        ("status", "string"),
        ("email", "string"),
        ("deleted_at", "datetime"),
    ]
    await api.studio.put(ORDERS, json=definition(RECENT, fields=retyped))
    report = await api.studio.get(f"{ORDERS}/schema")
    change = next(c for c in report["changes"] if c["op"] == "retype_column")
    await api.studio.post(
        f"{ORDERS}/schema/apply", json={"changes": [change["id"]], "confirm": "orders"}
    )
    after = await listed(api)
    assert after["by_tenant"]["state"] == "ok" and after["by_tenant"]["columns"] == [
        "tenant",
        "number",
    ]
    assert (await api.studio.get(f"{ORDERS}/schema"))["state"] == "in_sync"


async def test_declared_indexes_are_part_of_the_resource_and_its_blueprint(orders):
    api = orders
    await api.studio.put(ORDERS, json=definition(RECENT, OPEN))
    body = await api.studio.get(ORDERS)
    assert [i["name"] for i in body["indexes"]] == ["by_tenant", "open_only"]
    assert body["indexes"][0]["columns"][1] == {"name": "number", "desc": True}
    blueprint = await api.studio.get(f"{ENV}/blueprint")
    stored = next(r for r in blueprint["definitions"]["resources"] if r["name"] == "orders")
    assert [i["name"] for i in stored["indexes"]] == ["by_tenant", "open_only"]


async def test_a_resource_saved_before_indexes_existed_can_be_updated_from_what_the_api_returned(
    orders,
):
    from pawabase_server.db.models import Resource

    api = orders
    await Resource.filter(name="orders").update(
        indexes=None
    )  # what the migration left on an old row
    body = await api.studio.get(ORDERS)
    assert body["indexes"] == []  # reported as a list, so a client can send it straight back
    echoed = {
        k: v
        for k, v in body.items()
        if k not in ("id", "environment_id", "created_at", "updated_at")
    }
    await api.studio.put(ORDERS, json=echoed)
    await api.studio.put(ORDERS, json={**echoed, "indexes": None})  # and a null is tolerated too
