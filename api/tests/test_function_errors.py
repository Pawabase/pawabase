"""A function that raises: the caller gets a clean error, the run keeps what broke and where, and the log has one line, not a traceback."""

import logging
import textwrap

import pytest

from database.models import FunctionRun

ENV = "/platform/v1/envs/development"

CODE = textwrap.dedent(
    """
    from pawabase_core.functions import function

    def lookup(table):
        return {}["relation_" + table]

    @function("boom.fail", policy="public")
    async def fail(ctx):
        return lookup("activity")
    """
)


@pytest.fixture
async def boom(api, tmp_path):
    code = tmp_path / "code" / "functions"
    code.mkdir(parents=True)
    (code / "boom.py").write_text(CODE)
    await api.studio.post(
        f"{ENV}/routes",
        json={
            "method": "POST",
            "path": "/boom",
            "handler_type": "function",
            "handler": "boom.fail",
            "policy": "public",
        },
    )
    return api


async def test_a_failing_function_gives_a_clean_error_and_a_short_account(boom, caplog):
    caplog.set_level(logging.INFO)
    response = await boom.http.post(
        "/rest/v1/boom",
        json={},
        headers={**boom.context_headers("development"), "x-request-id": "req-77"},
    )
    body = response.json()
    assert response.status_code == 500 and body["error"] == "function_error"
    assert body["message"] == "function 'boom.fail' failed" and body["request_id"] == "req-77"
    assert "relation_activity" not in str(body) and "lookup" not in str(
        body
    )  # an end user is told nothing about the code

    run = await FunctionRun.filter(function="boom.fail").first()
    assert run.status == "failed"
    assert (
        "KeyError" in run.error and "lookup" in run.error and "boom.py" in run.error
    )  # what broke, and where in the function

    lines = [r.getMessage() for r in caplog.records if r.name == "pawabase.errors"]
    assert len(lines) == 1 and "request=req-77" in lines[0] and "boom.py" in lines[0]
    assert all(
        "Traceback" not in r.getMessage() and not r.exc_info
        for r in caplog.records
        if r.name == "pawabase.errors"
    )
