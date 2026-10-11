"""Moving an environment's data into another database."""

from pawabase_server.data.copy import copy_resource
from pawabase_server.data.source import DataSource
from pawabase_server.data.store import ResourceSpec

ENV = "/platform/v1/envs/development"


async def test_rows_are_copied_with_their_types_and_a_second_run_changes_nothing(api, tmp_path):
    definition = {
        "name": "orders",
        "fields": [
            {"name": "ref", "type": "string"},
            {"name": "total", "type": "number"},
            {"name": "paid", "type": "boolean"},
            {"name": "meta", "type": "json"},
            {"name": "placed", "type": "datetime"},
        ],
    }
    await api.studio.post(f"{ENV}/resources", json=definition)
    await api.studio.post(f"{ENV}/resources/orders/schema/apply", json={})
    for n in range(3):
        await api.studio.post(
            f"{ENV}/resources/orders/records",
            json={
                "ref": f"A{n}",
                "total": 9.5 + n,
                "paid": bool(n % 2),
                "meta": {"n": n, "tags": ["x"]},
                "placed": "2026-10-01T10:00:00+00:00",
            },
        )
    state = await api.platform.state("development")
    source = await state.source()
    spec = state.specs["orders"]

    target = await DataSource(f"sqlite://{tmp_path}/copy.db", "copy-to").connect()
    report = await copy_resource(source, target, spec)
    assert report["status"] == "copied" and report["rows"] == 3 and report["copied"] == 3

    rows = await target.fetch('SELECT "ref", "paid", "meta" FROM "orders" ORDER BY "ref"')
    assert [r["ref"] for r in rows] == ["A0", "A1", "A2"]
    again = await copy_resource(source, target, spec)
    assert again["copied"] == 0 and again["in_target"] == 3  # nothing doubled

    dry = await copy_resource(
        source, target, ResourceSpec(name="ghost", table="ghost"), dry_run=True
    )
    assert dry["status"] == "no table in the source"
    await target.close()
