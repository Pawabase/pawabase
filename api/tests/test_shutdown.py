"""Stopping the API must leave nothing running that could keep the process alive.

The request-metrics rollup flushes into the database when the platform stops. If the
database has already closed by then, that write opens a fresh SQLite connection, whose worker
thread is not a daemon: every test run that ended this way finished its tests and then never
exited, which is how `pytest` came to run for six hours in CI.
"""

import threading

from app.bootstrap import create_app


def database_threads() -> set[int]:
    return {
        thread.ident
        for thread in threading.enumerate()
        if "_connection_worker_thread" in thread.name and thread.is_alive()
    }


def test_the_platform_stops_before_the_database_closes(settings):
    app = create_app(settings)
    names = [handler.__name__ for handler in app.shutdown_handlers]
    assert names[0] == "stop_platform"
    assert len(names) > 1  # the database installable's own handler comes after it


async def test_shutdown_after_traffic_leaves_no_database_thread_behind(settings):
    from datetime import UTC, datetime

    from pawabase_core.telemetry import RequestRecord

    before = database_threads()
    app = create_app(settings)
    await app._startup()
    # An environment's request, waiting in the rollup for its last flush.
    await app.state["request_rollup"].record(
        RequestRecord(
            service="gateway",
            request_id="req_1",
            method="GET",
            path="/rest/v1/orders",
            route="/rest/v1/orders",
            status=200,
            duration_ms=3.0,
            started_at=datetime.now(UTC).isoformat(),
            env="development",
        )
    )
    await app._shutdown()
    assert database_threads() - before == set()
