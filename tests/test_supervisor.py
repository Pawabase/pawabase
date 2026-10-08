from pawabase_core.ensure_database import NAME
from pawabase_core.supervisor import SERVICES, database_url_for, environment_for


def test_akountz_gets_its_own_database_on_the_same_server():
    base = {"PAWABASE_DATABASE_URL": "postgres://pawabase:s3cret@postgres:5432/pawabase"}
    assert environment_for("api", base)["PAWABASE_DATABASE_URL"].endswith("/pawabase")
    assert (
        environment_for("akountz", base)["PAWABASE_DATABASE_URL"]
        == "postgres://pawabase:s3cret@postgres:5432/akountz"
    )
    explicit = {**base, "PAWABASE_AKOUNTZ_DATABASE_URL": "postgres://other@elsewhere/auth"}
    assert (
        environment_for("akountz", explicit)["PAWABASE_DATABASE_URL"]
        == "postgres://other@elsewhere/auth"
    )


def test_sqlite_is_left_alone_and_services_find_each_other_on_loopback():
    env = environment_for("akountz", {"PAWABASE_DATABASE_URL": "sqlite:///data/api.db"})
    assert env["PAWABASE_DATABASE_URL"] == "sqlite:///data/api.db"
    assert (
        env["PAWABASE_API_URL"] == "http://127.0.0.1:8001"
        and env["PAWABASE_GATEWAY_URL"] == "http://127.0.0.1:8080"
    )
    # an operator's own address wins
    assert (
        environment_for("gateway", {"PAWABASE_API_URL": "http://api.internal:9"})[
            "PAWABASE_API_URL"
        ]
        == "http://api.internal:9"
    )


def test_the_api_starts_first_and_every_service_is_listed_once():
    names = [name for name, *_ in SERVICES]
    assert names[0] == "api" and sorted(names) == sorted(
        {"api", "worker", "scheduler", "akountz", "angula", "gateway", "studio"}
    )


def test_database_names_are_validated_before_they_reach_sql():
    assert (
        NAME.match("akountz")
        and not NAME.match('x"; DROP DATABASE pawabase; --')
        and not NAME.match("1db")
    )
    assert database_url_for("postgres://u:p@h:5432/a", "b") == "postgres://u:p@h:5432/b"


# ── choosing the processes ───────────────────────────────────────────────

from pawabase_core.supervisor import selected  # noqa: E402


def names(environ):
    return [name for name, *_ in selected(environ)]


def test_by_default_every_process_runs():
    assert names({}) == ["api", "worker", "scheduler", "akountz", "angula", "gateway", "studio"]


def test_an_inline_worker_and_scheduler_do_not_get_processes_of_their_own():
    assert names({"PAWABASE_INLINE_WORKER": "true"}) == [
        "api",
        "scheduler",
        "akountz",
        "angula",
        "gateway",
        "studio",
    ]
    assert names({"PAWABASE_INLINE_WORKER": "1", "PAWABASE_INLINE_SCHEDULER": "yes"}) == [
        "api",
        "akountz",
        "angula",
        "gateway",
        "studio",
    ]


def test_false_or_unset_flags_leave_the_defaults_alone():
    assert len(names({"PAWABASE_INLINE_WORKER": "false", "PAWABASE_INLINE_SCHEDULER": ""})) == 7


def test_studio_can_be_left_out():
    assert "studio" not in names({"PAWABASE_STUDIO": "off"})
    assert "studio" in names({"PAWABASE_STUDIO": "on"})
    assert "studio" in names({"PAWABASE_STUDIO": "something-else"})


def test_an_explicit_list_runs_exactly_those_in_start_order():
    assert names({"PAWABASE_PROCESSES": "gateway, api ,akountz"}) == ["api", "akountz", "gateway"]
    # an explicit list is taken at its word: the inline flags do not trim it further
    assert names({"PAWABASE_PROCESSES": "api,worker", "PAWABASE_INLINE_WORKER": "true"}) == [
        "api",
        "worker",
    ]


def test_a_bad_list_is_refused_with_a_reason():
    import pytest

    with pytest.raises(ValueError, match="names gatewy"):
        selected({"PAWABASE_PROCESSES": "api,gatewy"})
    with pytest.raises(ValueError, match="cannot leave out the api"):
        selected({"PAWABASE_PROCESSES": "gateway,worker"})
