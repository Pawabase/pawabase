import asyncio

import pytest

from pawabase_core.ensure_database import NAME
from pawabase_core.supervisor import SERVICES, database_url_for, environment_for


def test_akountz_gets_its_own_database_on_the_same_server():
    base = {"PAWABASE_DATABASE_URL": "postgres://pawabase:s3cret@postgres:5432/pawabase"}
    assert environment_for("api", base)["PAWABASE_DATABASE_URL"].endswith("/pawabase")
    assert environment_for("akountz", base)["PAWABASE_DATABASE_URL"] == "postgres://pawabase:s3cret@postgres:5432/akountz"
    explicit = {**base, "PAWABASE_AKOUNTZ_DATABASE_URL": "postgres://other@elsewhere/auth"}
    assert environment_for("akountz", explicit)["PAWABASE_DATABASE_URL"] == "postgres://other@elsewhere/auth"


def test_sqlite_is_left_alone_and_services_find_each_other_on_loopback():
    env = environment_for("akountz", {"PAWABASE_DATABASE_URL": "sqlite:///data/api.db"})
    assert env["PAWABASE_DATABASE_URL"] == "sqlite:///data/api.db"
    assert env["PAWABASE_API_URL"] == "http://127.0.0.1:8001" and env["PAWABASE_GATEWAY_URL"] == "http://127.0.0.1:8080"
    # an operator's own address wins
    assert environment_for("gateway", {"PAWABASE_API_URL": "http://api.internal:9"})["PAWABASE_API_URL"] == "http://api.internal:9"


def test_the_api_starts_first_and_every_service_is_listed_once():
    names = [name for name, *_ in SERVICES]
    assert names[0] == "api" and sorted(names) == sorted({"api", "worker", "scheduler", "akountz", "angula", "gateway", "studio"})


def test_database_names_are_validated_before_they_reach_sql():
    assert NAME.match("akountz") and not NAME.match('x"; DROP DATABASE pawabase; --') and not NAME.match("1db")
    assert database_url_for("postgres://u:p@h:5432/a", "b") == "postgres://u:p@h:5432/b"
