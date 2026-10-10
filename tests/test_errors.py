"""The handler every service installs: one log line, a clean answer, no traceback."""

import logging

from sillo import SilloApp, abort
from sillo.testclient import TestClient

from pawabase_core import errors


def raises_deep():
    {}["missing"]  # noqa: B018


def calls_deep():
    raises_deep()


def make_app(debug=False):
    app = SilloApp(debug=False)
    errors.install(app, "test", debug=debug)

    @app.get("/boom")
    async def boom(ctx):
        calls_deep()

    @app.get("/chosen")
    async def chosen(ctx):
        raise errors.PawabaseError(
            "That is not allowed.", status=403, code="forbidden", hint="Ask an admin."
        )

    @app.get("/gone")
    async def gone(ctx):
        abort(404, detail="no such thing")

    return app


def test_an_unexpected_failure_is_one_log_line_and_a_clean_answer(caplog):
    caplog.set_level(logging.INFO)
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"x-request-id": "req-1"})
    body = response.json()
    assert (
        response.status_code == 500
        and body["error"] == "internal_error"
        and body["request_id"] == "req-1"
    )
    assert (
        "calls_deep" not in str(body) and "KeyError" not in str(body) and "failure" not in body
    )  # nothing internal for an ordinary caller
    lines = [r for r in caplog.records if r.name == "pawabase.errors"]
    assert (
        len(lines) == 1
        and "raises_deep" in lines[0].getMessage()
        and "request=req-1" in lines[0].getMessage()
    )
    assert lines[0].exc_info is None  # no traceback in the log


def test_debug_shows_where_it_broke():
    with TestClient(make_app(debug=True), raise_server_exceptions=False) as client:
        failure = client.get("/boom").json()["failure"]
    assert failure["kind"] == "KeyError" and failure["where"][0].endswith("in raises_deep")


def test_tracebacks_come_back_when_asked_for(caplog, monkeypatch):
    monkeypatch.setenv("PAWABASE_TRACEBACKS", "true")
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        client.get("/boom")
    assert any(r.exc_info for r in caplog.records if r.name == "pawabase.errors")


def test_a_failure_a_service_chose_keeps_its_status_code_and_hint():
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        response = client.get("/chosen")
    assert response.status_code == 403
    assert response.json() == {
        "error": "forbidden",
        "message": "That is not allowed.",
        "hint": "Ask an admin.",
    }


def test_ordinary_http_errors_are_left_alone():
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        assert client.get("/gone").status_code == 404
        assert client.get("/no-such-route").status_code == 404


def test_logged_exceptions_print_the_short_account(caplog):
    errors.compact_logging()
    log = logging.getLogger("pawabase.test")
    handler = logging.StreamHandler(stream := __import__("io").StringIO())
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    try:
        calls_deep()
    except KeyError:
        log.exception("could not do the thing")
    finally:
        log.removeHandler(handler)
    text = stream.getvalue()
    assert (
        "could not do the thing" in text
        and "raises_deep" in text
        and text.rstrip().endswith("KeyError: 'missing'")
    )
    assert "Traceback (most recent call last)" not in text
