"""A resource's table against its definition: drift, the history of changes, and rolling them back."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"
BOOKS = f"{ENV}/resources/books"


def definition(*fields):
    return {
        "name": "books",
        "fields": [{"name": n, "type": t} for n, t in fields],
        "operations": {"list": {"enabled": True, "policy": "public"}},
    }


@pytest.fixture
async def books(api):
    await api.studio.post(
        f"{ENV}/resources", json=definition(("title", "string"), ("pages", "integer"))
    )
    return api


async def schema(api):
    return await api.studio.get(f"{BOOKS}/schema")


async def rows(api):
    return (await api.studio.get(f"{BOOKS}/records"))["data"]


async def test_a_new_resource_has_no_table_until_it_is_applied(books):
    api = books
    report = await schema(api)
    assert report["state"] == "no_table" and report["changes"][0]["op"] == "create_table"
    assert (await api.studio.get(f"{ENV}/schema"))["pending"] == 1

    applied = await api.studio.post(f"{BOOKS}/schema/apply", json={})
    assert applied["applied"] and applied["migration"]["number"] == 1
    report = await schema(api)
    assert report["state"] == "in_sync" and report["changes"] == []
    nothing = await api.studio.post(f"{BOOKS}/schema/apply", json={})
    assert nothing["applied"] is False

    history = (await api.studio.get(f"{BOOKS}/migrations"))["data"]
    assert [(h["number"], h["status"], h["can_roll_back"]) for h in history] == [
        (1, "applied", True)
    ]


async def test_an_added_field_is_pending_and_can_be_rolled_back(books):
    api = books
    await api.studio.post(f"{BOOKS}/schema/apply", json={})
    await api.studio.post(f"{BOOKS}/records", json={"title": "Dune", "pages": 412})

    await api.studio.put(
        BOOKS, json=definition(("title", "string"), ("pages", "integer"), ("isbn", "string"))
    )
    report = await schema(api)
    assert report["state"] == "pending"
    assert [c["id"] for c in report["changes"]] == ["add_column:isbn"]

    applied = await api.studio.post(f"{BOOKS}/schema/apply", json={})
    assert applied["migration"]["number"] == 2
    await api.studio.patch(f"{BOOKS}/records/{(await rows(api))[0]['id']}", json={"isbn": "123"})
    assert (await rows(api))[0]["isbn"] == "123"

    # Only the newest migration can be undone.
    history = (await api.studio.get(f"{BOOKS}/migrations"))["data"]
    first, second = history[1], history[0]
    assert second["can_roll_back"] and not first["can_roll_back"]
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{BOOKS}/migrations/{first['id']}/rollback")
    assert refused.value.status == 422

    await api.studio.post(f"{BOOKS}/migrations/{second['id']}/rollback")
    row = (await rows(api))[0]
    assert row["title"] == "Dune" and "isbn" not in row  # the column is kept aside, not served
    assert (await schema(api))["state"] == "pending"  # the definition still has the field
    kept = (await schema(api))["retained"]
    assert [item["original"] for item in kept] == ["isbn"]

    history = (await api.studio.get(f"{BOOKS}/migrations"))["data"]
    assert [(h["kind"], h["status"]) for h in history] == [
        ("rollback", "applied"),
        ("apply", "rolled_back"),
        ("apply", "applied"),
    ]


async def test_removing_or_retyping_a_field_keeps_the_values_until_purged(books):
    api = books
    await api.studio.post(f"{BOOKS}/schema/apply", json={})
    await api.studio.post(f"{BOOKS}/records", json={"title": "Dune", "pages": 412})

    await api.studio.put(BOOKS, json=definition(("title", "text"), ("pages", "string")))
    report = await schema(api)
    assert report["state"] == "review"  # nothing here only adds
    assert [c["id"] for c in report["changes"]] == ["retype_column:pages"]

    # Changes that rewrite values are not applied by default, and need the resource's name.
    assert (await api.studio.post(f"{BOOKS}/schema/apply", json={}))["applied"] is False
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{BOOKS}/schema/apply", json={"changes": ["retype_column:pages"]})
    assert refused.value.status == 422
    await api.studio.post(
        f"{BOOKS}/schema/apply", json={"changes": ["retype_column:pages"], "confirm": "books"}
    )
    assert (await rows(api))[0]["pages"] == "412"
    assert (await schema(api))["state"] == "in_sync"

    migration = (await api.studio.get(f"{BOOKS}/migrations"))["data"][0]
    await api.studio.post(f"{BOOKS}/migrations/{migration['id']}/rollback")
    assert (await rows(api))[0]["pages"] == 412  # the original values came back

    # Drop a field, apply, then purge: the rollback is gone with the data.
    await api.studio.put(BOOKS, json=definition(("title", "text")))
    dropped = [c["id"] for c in (await schema(api))["changes"]]
    assert "drop_column:pages" in dropped
    await api.studio.post(
        f"{BOOKS}/schema/apply", json={"changes": ["drop_column:pages"], "confirm": "books"}
    )
    assert "pages" not in (await rows(api))[0]
    purged = await api.studio.post(f"{BOOKS}/schema/purge", json={"confirm": "books"})
    assert purged["removed"]
    latest = (await api.studio.get(f"{BOOKS}/migrations"))["data"][0]
    assert not latest["can_roll_back"] and "purged" in latest["cannot_roll_back"]


async def test_a_change_that_fails_is_undone_and_recorded(books):
    api = books
    await api.studio.post(f"{BOOKS}/schema/apply", json={})
    await api.studio.post(f"{BOOKS}/records", json={"title": "Dune", "pages": 412})
    await api.studio.put(
        BOOKS, json=definition(("title", "string"), ("pages", "integer"), ("x", "string"))
    )
    source = await (await api.platform.state("development")).source()
    original = source.execute

    async def failing(sql, params=None):
        if "ADD COLUMN" in sql:
            raise RuntimeError("disk full")
        return await original(sql, params)

    source.execute = failing
    try:
        with pytest.raises(ServiceError):
            await api.studio.post(f"{BOOKS}/schema/apply", json={})
    finally:
        source.execute = original
    history = (await api.studio.get(f"{BOOKS}/migrations"))["data"]
    assert history[0]["status"] == "failed" and "disk full" in history[0]["error"]
    assert (await rows(api))[0]["title"] == "Dune"


async def test_changes_made_outside_pawabase_are_noticed_and_can_be_adopted(books):
    api = books
    await api.studio.post(f"{BOOKS}/schema/apply", json={})
    assert (await schema(api))["external"] == []
    source = await (await api.platform.state("development")).source()

    await source.execute('ALTER TABLE "books" ADD COLUMN "rating" INTEGER')
    report = await schema(api)
    assert report["state"] == "external"
    assert [(c["kind"], c["column"], c["field_type"]) for c in report["external"]] == [
        ("added", "rating", "integer")
    ]
    assert [f["name"] for f in report["proposal"]] == ["title", "pages", "rating"]

    # A rename is one change, not a drop and an add.
    await source.execute('ALTER TABLE "books" RENAME COLUMN "pages" TO "page_count"')
    kinds = {(c["kind"], c["column"]) for c in (await schema(api))["external"]}
    assert ("renamed", "page_count") in kinds and ("added", "rating") in kinds

    # Follow the table: update the definition (what Studio does), then acknowledge.
    proposal = (await schema(api))["proposal"]
    await api.studio.put(BOOKS, json={**definition(), "fields": proposal})
    await api.studio.post(f"{BOOKS}/schema/acknowledge", json={})
    report = await schema(api)
    assert report["external"] == [] and report["state"] == "in_sync"
    assert [f["name"] for f in report["proposal"] or []] == []
    history = (await api.studio.get(f"{BOOKS}/migrations"))["data"]
    assert history[0]["kind"] == "external" and not history[0]["can_roll_back"]


async def test_a_table_nobody_defined_can_become_a_resource(api):
    source = await (await api.platform.state("development")).source()
    await source.execute(
        'CREATE TABLE "legacy_orders" ("id" CHAR(26) PRIMARY KEY, "total" REAL, '
        '"paid" INTEGER, "note" TEXT, "created_at" TEXT, "updated_at" TEXT)'
    )
    listed = (await api.studio.get(f"{ENV}/schema/tables"))["data"]
    table = next(item for item in listed if item["table"] == "legacy_orders")
    definition_ = table["definition"]
    assert definition_["primary_key"] == "id" and definition_["id_type"] == "ulid"
    assert definition_["timestamps"] is True
    assert [f["name"] for f in definition_["fields_"]] == ["total", "paid", "note"]

    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "legacy_orders",
            "table": "legacy_orders",
            "primary_key": "id",
            "id_type": "ulid",
            "timestamps": True,
            "fields": definition_["fields_"],
        },
    )
    path = f"{ENV}/resources/legacy_orders"
    await api.studio.post(f"{path}/schema/acknowledge", json={"note": "Imported from the table"})
    report = await api.studio.get(f"{path}/schema")
    assert report["state"] == "in_sync" and report["external"] == []
    assert all(
        t["table"] != "legacy_orders"
        for t in (await api.studio.get(f"{ENV}/schema/tables"))["data"]
    )


async def test_a_table_keyed_by_integers_is_explained_not_imported(api):
    source = await (await api.platform.state("development")).source()
    await source.execute(
        'CREATE TABLE "old_things" ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "name" TEXT)'
    )
    listed = (await api.studio.get(f"{ENV}/schema/tables"))["data"]
    table = next(item for item in listed if item["table"] == "old_things")
    assert table["definition"] is None and "ULID or a UUID" in table["problem"]
