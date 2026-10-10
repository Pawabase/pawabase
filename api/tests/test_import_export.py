"""Exporting a resource's records and importing rows into it, from Studio."""

ENV = "/platform/v1/envs/development"


async def _notes(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [
                {"name": "title", "type": "string", "required": True},
                {"name": "stars", "type": "integer"},
            ],
            "operations": {"list": {"enabled": True, "policy": "public"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")


async def test_rows_are_validated_and_imported(api):
    await _notes(api)
    rows = [
        {"title": "one", "stars": 3},
        {"stars": 1},
        {"title": "three", "stars": "many"},
        {"title": "four"},
    ]
    check = await api.studio.post(
        f"{ENV}/resources/notes/import", json={"rows": rows, "dry_run": True}
    )
    assert (check["created"], check["failed"], check["dry_run"]) == (2, 2, True)
    assert {e["row"] for e in check["errors"]} == {2, 3}
    assert (await api.studio.get(f"{ENV}/resources/notes/records"))["total"] == 0

    done = await api.studio.post(f"{ENV}/resources/notes/import", json={"rows": rows})
    assert (done["created"], done["failed"]) == (2, 2)
    stored = (await api.studio.get(f"{ENV}/resources/notes/records"))["data"]
    assert sorted(r["title"] for r in stored) == ["four", "one"]


async def test_upsert_updates_the_row_whose_key_is_given(api):
    await _notes(api)
    await api.studio.post(
        f"{ENV}/resources/notes/import", json={"rows": [{"title": "draft", "stars": 1}]}
    )
    [first] = (await api.studio.get(f"{ENV}/resources/notes/records"))["data"]
    done = await api.studio.post(
        f"{ENV}/resources/notes/import",
        json={"mode": "upsert", "rows": [{"id": first["id"], "stars": 5}, {"title": "new"}]},
    )
    assert (done["created"], done["updated"], done["failed"]) == (1, 1, 0)
    rows = {
        r["title"]: r["stars"]
        for r in (await api.studio.get(f"{ENV}/resources/notes/records"))["data"]
    }
    assert rows == {"draft": 5, "new": None}


async def test_export_returns_every_record_and_says_when_it_stopped_early(api):
    await _notes(api)
    await api.studio.post(
        f"{ENV}/resources/notes/import", json={"rows": [{"title": f"n{i}"} for i in range(7)]}
    )
    everything = await api.studio.get(f"{ENV}/resources/notes/export")
    assert len(everything["data"]) == 7 and everything["truncated"] is False
    assert {"id", "title", "stars"} <= set(everything["columns"])
    part = await api.studio.get(f"{ENV}/resources/notes/export", params={"limit": 3})
    assert len(part["data"]) == 3 and part["truncated"] is True and part["total"] == 7
