"""A failure is described by its own frames, and known ones come with what to try."""

import json as library

import pytest

from pawabase_core.failures import hint_for, locate, report, root_cause


def raises_deep():
    {}["missing"]  # noqa: B018


def calls_deep():
    raises_deep()


def test_a_failure_is_described_by_its_own_frames_not_the_librarys():
    try:
        calls_deep()
    except KeyError as exc:
        failure = report(exc)
    assert failure.kind == "KeyError" and failure.message == "'missing'"
    assert [place.function for place in failure.where][:2] == [
        "raises_deep",
        "calls_deep",
    ]  # innermost first
    assert failure.line().startswith("KeyError: 'missing' at ") and "raises_deep" in failure.line()
    trace = failure.trace()
    assert trace.splitlines()[-1] == "KeyError: 'missing'" and trace.index(
        "calls_deep"
    ) < trace.index("raises_deep")  # outermost first, error last
    assert "site-packages" not in trace and "Traceback" not in trace


def test_the_error_at_the_bottom_of_a_chain_is_the_one_reported():
    try:
        try:
            calls_deep()
        except KeyError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as exc:
        assert root_cause(exc).__class__ is KeyError
        assert report(exc).kind == "KeyError" and "raises_deep" in report(exc).line()


def test_library_frames_are_dropped():
    try:
        library.loads("{")  # raises three frames deep inside the standard library
    except ValueError as exc:
        places = locate(exc)
    assert [place.function for place in places] == ["test_library_frames_are_dropped"]


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ('OperationalError relation "activity" does not exist', "Migrate"),
        ("OSError Connect call failed ('10.0.0.1', 5432)", "could not be reached"),
        ("InvalidPasswordError password authentication failed for user", "username or password"),
        ("TooManyConnectionsError sorry, too many clients already", "PAWABASE_DB_POOL_MAX"),
        ("IntegrityError duplicate key value violates unique constraint", "already exists"),
    ],
)
def test_known_failures_come_with_what_to_try(text, fragment):
    assert fragment in hint_for(text)


def test_an_unknown_failure_has_no_invented_hint():
    assert hint_for("SomethingOdd went sideways") is None
