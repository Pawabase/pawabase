"""SQL that changes the database's structure comes back with what it means and how to put it right."""

import pytest

from pawabase_server.data.ddl import parse

ENV = "/platform/v1/envs/development"


def test_statements_are_read_for_the_tables_they_touch():
    assert parse('CREATE TABLE IF NOT EXISTS "main"."tickets" (id int)') == [
        {"action": "create_table", "table": "tickets"}
    ]
    assert parse("ALTER TABLE tickets RENAME TO cases") == [
        {"action": "rename_table", "table": "tickets", "to": "cases"}
    ]
    assert parse("-- note\nALTER TABLE `tickets` ADD COLUMN x TEXT; DROP TABLE tickets")[1] == {
        "action": "drop_table",
        "table": "tickets",
    }
    assert parse("SELECT * FROM tickets") == []
    assert parse("INSERT INTO tickets VALUES (1)") == []


async def sql(api, text):
    return await api.studio.post(f"{ENV}/database/query", json={"sql": text, "allow_write": True})


@pytest.fixture
async def tickets(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "tickets",
            "fields": [{"name": "subject", "type": "string"}],
            "operations": {"list": {"enabled": True, "policy": "public"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/tickets/schema/apply", json={})
    return api


async def test_a_new_table_says_how_to_import_it(api):
    result = await sql(api, 'CREATE TABLE notes ("id" CHAR(26) PRIMARY KEY, "text" TEXT)')
    [warning] = result["warnings"]
    assert warning["level"] == "warn" and "notes" in warning["title"]
    assert warning["action"]["type"] == "import" and len(warning["steps"]) >= 3

    plain = await sql(api, 'CREATE TABLE legacy ("id" INTEGER PRIMARY KEY, "x" TEXT)')
    assert plain["warnings"][0]["level"] == "info" and "ULID" in plain["warnings"][0]["steps"][1]


async def test_changing_a_resources_table_points_at_its_schema(tickets):
    api = tickets
    altered = await sql(api, 'ALTER TABLE "tickets" ADD COLUMN "assignee" TEXT')
    [warning] = altered["warnings"]
    assert warning["resource"] == "tickets" and warning["action"]["type"] == "schema"
    assert warning["changes"][0]["column"] == "assignee"

    renamed = await sql(api, 'ALTER TABLE "tickets" RENAME TO "cases"')
    assert renamed["warnings"][0]["level"] == "error"
    assert any("RENAME TO" in step for step in renamed["warnings"][0]["steps"])

    await sql(api, 'ALTER TABLE "cases" RENAME TO "tickets"')
    dropped = await sql(api, 'DROP TABLE "tickets"')
    assert (
        dropped["warnings"][0]["level"] == "error"
        and "backup" in " ".join(dropped["warnings"][0]["steps"]).lower()
    )


async def test_plain_data_changes_say_nothing(tickets):
    result = await sql(
        tickets, "INSERT INTO tickets (id, subject) VALUES ('01JTESTTICKET000000000000A1', 'x')"
    )
    assert "warnings" not in result
