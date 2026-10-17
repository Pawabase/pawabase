"""Dropping a table from the table editor's Settings tab: a plain table goes, an internal one is protected,
and one still backing a resource must have its definition removed first."""

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"


async def create_plain_table(api, name):
    await api.studio.post(
        f"{ENV}/database/query",
        json={"sql": f'CREATE TABLE "{name}" ("id" INTEGER PRIMARY KEY)', "allow_write": True},
    )


async def tables(api):
    overview = await api.studio.get(f"{ENV}/database")
    return {t["name"] for t in overview["tables"]}


async def test_dropping_a_plain_table_removes_it(api):
    await create_plain_table(api, "scratch")
    assert "scratch" in await tables(api)
    await api.studio.delete(f"{ENV}/database/tables/scratch")
    assert "scratch" not in await tables(api)


async def test_an_unknown_table_is_refused(api):
    try:
        await api.studio.delete(f"{ENV}/database/tables/nope")
    except ServiceError as exc:
        assert exc.status == 404
    else:
        raise AssertionError("an unknown table was accepted")


async def test_an_internal_table_is_protected(api):
    try:
        await api.studio.delete(f"{ENV}/database/tables/pb_leases")
    except ServiceError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("an internal table was accepted")


async def test_a_resource_backed_table_must_have_its_definition_removed_first(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={"name": "widgets", "fields": [{"name": "label", "type": "string"}]},
    )
    await api.studio.post(f"{ENV}/resources/widgets/migrate")
    assert "widgets" in await tables(api)
    try:
        await api.studio.delete(f"{ENV}/database/tables/widgets")
    except ServiceError as exc:
        assert exc.status == 409 and "widgets" in str(exc.body)
    else:
        raise AssertionError("a resource-backed table was dropped directly")
    await api.studio.delete(f"{ENV}/resources/widgets")
    await api.studio.delete(f"{ENV}/database/tables/widgets")
    assert "widgets" not in await tables(api)
