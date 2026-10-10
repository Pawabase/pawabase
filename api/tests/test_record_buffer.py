import logging

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
