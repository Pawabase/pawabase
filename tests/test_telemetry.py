"""Request spans and captured logs."""

import contextvars
import logging
import time

import pytest

from pawabase_core import telemetry
from pawabase_core.telemetry import MAX_LOGS, MAX_SPANS, RequestLogHandler, span


@pytest.fixture
def request_scope():
    """A fake in-flight request: a trace dict and an origin, as the recorder sets them."""
    trace: dict = {}
    tokens = (telemetry._trace.set(trace), telemetry._origin.set(time.perf_counter()))
    yield trace
    telemetry._origin.reset(tokens[1])
    telemetry._trace.reset(tokens[0])


def test_spans_do_nothing_outside_a_request():
    with span("db", "SELECT 1") as step:
        step.set(rows=1)
    assert telemetry.current_notes() is None


def test_spans_nest_and_time_their_work(request_scope):
    with span("db", "SELECT 1", op="fetch") as outer:
        time.sleep(0.01)
        with span("cache", "get k"):
            pass
        outer.set(rows=3)
    first, second = request_scope["spans"]
    assert (first["id"], first["parent"], first["kind"]) == (1, None, "db")
    assert (second["id"], second["parent"]) == (2, 1)
    assert first["duration_ms"] >= 10 and first["attrs"] == {"op": "fetch", "rows": 3}
    assert second["start_ms"] >= first["start_ms"]


def test_a_span_that_raises_is_marked_and_the_exception_still_propagates(request_scope):
    with pytest.raises(ValueError), span("http", "GET x"):
        raise ValueError("boom")
    entry = request_scope["spans"][0]
    assert entry["status"] == "error" and entry["attrs"]["error"] == "ValueError: boom"
    # The failure does not leave a stale parent behind.
    with span("db", "next"):
        pass
    assert request_scope["spans"][1]["parent"] is None


def test_spans_are_capped_and_the_overflow_is_counted(request_scope):
    for n in range(MAX_SPANS + 5):
        with span("db", f"q{n}"):
            pass
    assert len(request_scope["spans"]) == MAX_SPANS and request_scope["spans_dropped"] == 5


def test_spans_in_concurrent_requests_do_not_mix():
    def run(label):
        trace: dict = {}
        telemetry._trace.set(trace)
        telemetry._origin.set(time.perf_counter())
        with span("db", label):
            pass
        return trace["spans"][0]["name"]

    assert [contextvars.copy_context().run(run, name) for name in ("a", "b")] == ["a", "b"]


@pytest.fixture
def log_capture():
    handler = RequestLogHandler()
    logger = logging.getLogger("pawabase.test.capture")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger
    logger.removeHandler(handler)


def test_log_lines_during_a_request_land_on_its_trace(request_scope, log_capture):
    log_capture.debug("too quiet")
    log_capture.info("loaded %s rows", 12)
    log_capture.warning("slow")
    assert [(e["level"], e["message"]) for e in request_scope["logs"]] == [
        ("info", "loaded 12 rows"),
        ("warning", "slow"),
    ]
    assert request_scope["logs"][0]["logger"] == "pawabase.test.capture"
    assert request_scope["logs"][0]["at_ms"] >= 0


def test_logs_outside_a_request_and_runtime_noted_lines_are_ignored(request_scope, log_capture):
    log_capture.info("already noted", extra={"pawabase": {"message": "already noted"}})
    assert "logs" not in request_scope
    telemetry._trace.set(None)
    log_capture.info("no request")  # must not raise


def test_an_error_with_an_exception_gives_the_request_its_error_and_traceback(
    request_scope, log_capture
):
    try:
        raise KeyError("sku")
    except KeyError:
        log_capture.exception("handler failed")
    assert request_scope["error"] == "KeyError: 'sku'"
    # The short account: the frames of the code that failed, then the error. Not the framework's whole traceback.
    trace = request_scope["traceback"]
    assert "test_an_error_with_an_exception" in trace and trace.endswith("KeyError: 'sku'")
    assert "Traceback (most recent call last)" not in trace


def test_logs_are_capped(request_scope, log_capture):
    for n in range(MAX_LOGS + 3):
        log_capture.info("line %s", n)
    assert len(request_scope["logs"]) == MAX_LOGS and request_scope["logs_dropped"] == 3
