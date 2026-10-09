import logging
import textwrap

import pytest

from app.record_buffer import RecordBuffer
from database.models import FunctionRun
from pawabase_core.ids import new_ulid

ENV = "/platform/v1/envs/development"


def run(function="f", **fields):
    return FunctionRun(
        id=new_ulid(),
        env="development",
        function=function,
        trigger="http",
        status="succeeded",
        input={},
        output={},
        logs=[],
        **fields,
    )


async def stored(function=None):
    query = FunctionRun.filter(function=function) if function else FunctionRun.all()
    return await query.count()


async def test_records_wait_for_the_flush_and_then_all_arrive_together(api):
    buffer = RecordBuffer(interval=60)
    for _ in range(3):
        await buffer.add(run("held"))
    assert (
        await stored("held") == 0 and len(buffer.pending) == 3
    )  # nothing written on the request's path
    await buffer.flush()
    assert await stored("held") == 3 and buffer.written == 3 and buffer.pending == []


async def test_a_full_batch_is_written_without_waiting_for_the_timer(api):
    buffer = RecordBuffer(interval=60, batch=5)
    for _ in range(5):
        await buffer.add(run("batch"))
    await buffer._kicked
    assert await stored("batch") == 5


async def test_an_interval_of_zero_writes_at_once(api):
    buffer = RecordBuffer(interval=0)
    await buffer.add(run("now"))
    assert await stored("now") == 1


async def test_stopping_writes_what_is_left(api):
    buffer = RecordBuffer(interval=60)
    buffer.start()
    await buffer.add(run("left"))
    await buffer.stop()
    assert await stored("left") == 1


async def test_the_buffer_is_bounded_and_counts_what_it_dropped(api):
    buffer = RecordBuffer(interval=60, batch=10_000, limit=3)
    for number in range(5):
        await buffer.add(run("capped", duration_ms=number))
    assert len(buffer.pending) == 3 and buffer.dropped == 2
    assert [record.duration_ms for record in buffer.pending] == [2, 3, 4]  # the oldest went


async def test_a_failed_write_is_one_short_log_line_and_not_an_error(api, caplog):
    caplog.set_level(logging.ERROR)
    buffer = RecordBuffer(interval=60)
    twin = run("twin")
    await buffer.add(twin)
    await buffer.add(
        FunctionRun(
            id=twin.id,
            env="development",
            function="twin",
            trigger="http",
            status="s",
            input={},
            output={},
            logs=[],
        )
    )
    await buffer.flush()  # the same id twice: the write fails, and that must not raise
    lines = [r for r in caplog.records if r.name == "pawabase.api.records"]
    assert (
        len(lines) == 1
        and "could not record 2 FunctionRun" in lines[0].getMessage()
        and not lines[0].exc_info
    )


CODE = textwrap.dedent(
    """
    from pawabase_core.functions import function

    @function("quick.ok", policy="public")
    async def ok(ctx):
        return {"ok": True}
    """
)


@pytest.fixture
def buffered(settings):
    return settings.model_copy(update={"record_buffer_seconds": 60})


async def test_a_function_call_does_not_wait_for_its_run_record(buffered, tmp_path):
    """With the buffer on, the call returns first and the record follows with the flush (and at shutdown)."""
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from pawabase_core.clients import ServiceClient
    from pawabase_core.functions import clear_functions

    code = tmp_path / "code" / "functions"
    code.mkdir(parents=True)
    (code / "quick.py").write_text(CODE)
    clear_functions()
    app = create_app(buffered)
    await app._startup()
    try:
        studio = ServiceClient(
            "http://api.test",
            secret=buffered.internal_secret,
            issuer="studio",
            audience="api",
            app=app,
        )
        await studio.post(
            f"{ENV}/routes",
            json={
                "method": "POST",
                "path": "/quick",
                "handler_type": "function",
                "handler": "quick.ok",
                "policy": "public",
            },
        )
        http = AsyncTestClient(app, base_url="http://api.test")
        from pawabase_core.context import CONTEXT_HEADER, PlatformContext
        from pawabase_core.tokens import issue_context_token

        header = {
            CONTEXT_HEADER: issue_context_token(
                buffered.internal_secret,
                PlatformContext(env="development", role="anon", key_id="t", scopes=()),
            )
        }
        assert (await http.post("/rest/v1/quick", json={}, headers=header)).status_code == 200
        platform = app.state["platform"]
        assert (
            await FunctionRun.filter(function="quick.ok").count() == 0
            and len(platform.records.pending) == 1
        )
        await platform.records.flush()
        assert await FunctionRun.filter(function="quick.ok").count() == 1
        await studio.close()
        await http.aclose()
    finally:
        await app._shutdown()
        clear_functions()
