"""The table editor's row browsing: paging, sorting and filtering, with column names checked against the table."""

ENV = "/platform/v1/envs/development"

NOTES = {
    "name": "notes",
    "fields": [
        {"name": "title", "type": "string", "required": True},
        {"name": "score", "type": "integer"},
    ],
    "operations": {
        "create": {"enabled": True, "policy": "public"},
        "list": {"enabled": True, "policy": "public"},
    },
}


async def seed(api):
    await api.studio.post(f"{ENV}/resources", json=NOTES)
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    service = api.context_headers("development", role="service")
    for title, score in (("apple", 3), ("banana", 1), ("cherry", 2), ("apricot", None)):
        body = {"title": title, **({"score": score} if score is not None else {})}
        created = await api.http.post("/rest/v1/notes", json=body, headers=service)
        assert created.status_code == 201, created.text


async def rows(api, **params):
    return await api.studio.get(f"{ENV}/database/tables/notes/rows", params=params)


async def test_sort_and_page(api):
    await seed(api)
    ordered = await rows(api, sort="title", order="asc")
    assert [r["title"] for r in ordered["data"]] == ["apple", "apricot", "banana", "cherry"]
    assert ordered["total"] is None  # unfiltered: the caller already knows the table's size
    page = await rows(api, sort="title", order="desc", limit=2, offset=1)
    assert [r["title"] for r in page["data"]] == ["banana", "apricot"]


async def test_filters(api):
    await seed(api)
    contains = await rows(
        api, filter_column="title", filter_op="contains", filter_value="ap", sort="title"
    )
    assert [r["title"] for r in contains["data"]] == ["apple", "apricot"] and contains["total"] == 2
    equals = await rows(api, filter_column="score", filter_op="eq", filter_value="2")
    assert [r["title"] for r in equals["data"]] == ["cherry"]
    empty = await rows(api, filter_column="score", filter_op="null")
    assert [r["title"] for r in empty["data"]] == ["apricot"]
    present = await rows(api, filter_column="score", filter_op="not_null")
    assert present["total"] == 3


async def test_unknown_columns_are_refused(api):
    from pawabase_server.core.clients import ServiceError

    await seed(api)
    for params in (
        {"sort": "title; DROP TABLE notes"},
        {"filter_column": "nope", "filter_value": "x"},
        {"filter_column": "title", "filter_op": "like"},
    ):
        try:
            await rows(api, **params)
        except ServiceError as exc:
            assert exc.status == 400
        else:
            raise AssertionError(f"{params} was accepted")
    assert len((await rows(api))["data"]) == 4  # the table is still there
