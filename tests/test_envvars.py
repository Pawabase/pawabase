import pytest

from pawabase_core import envvars


def test_prefix_normalises_punctuation_and_case():
    assert envvars.prefix_for("production") == "PRODUCTION"
    assert envvars.prefix_for("my-env") == "MY_ENV"
    assert envvars.prefix_for("eu.west 1") == "EU_WEST_1"


def test_get_reads_only_the_environments_own_variable():
    environ = {"PRODUCTION_MAIL_HOST": "smtp.prod", "PAWABASE_MAIL_HOST": "smtp.global"}
    assert envvars.get("production", "MAIL_HOST", environ) == "smtp.prod"
    # The global fallback is the caller's settings object, not read here.
    assert envvars.get("staging", "MAIL_HOST", environ) is None


def test_an_empty_value_counts_as_unset():
    assert envvars.get("production", "MAIL_HOST", {"PRODUCTION_MAIL_HOST": ""}) is None
    assert (
        envvars.text("production", "MAIL_HOST", "fallback", {"PRODUCTION_MAIL_HOST": ""})
        == "fallback"
    )


def test_integers_booleans_and_their_errors():
    environ = {
        "PROD_MAX_USERS": " 25 ",
        "PROD_MAIL_USE_TLS": "Yes",
        "PROD_MAIL_USE_SSL": "off",
        "PROD_RATE_LIMIT": "ten",
    }
    assert envvars.integer("prod", "MAX_USERS", 0, environ) == 25
    assert envvars.integer("prod", "DAILY_REQUEST_LIMIT", 7, environ) == 7
    assert envvars.boolean("prod", "MAIL_USE_TLS", False, environ) is True
    assert envvars.boolean("prod", "MAIL_USE_SSL", True, environ) is False
    with pytest.raises(ValueError, match="PROD_RATE_LIMIT must be a whole number"):
        envvars.integer("prod", "RATE_LIMIT", 0, environ)
    with pytest.raises(ValueError, match="true or false"):
        envvars.boolean("prod", "MAX_USERS", False, {"PROD_MAX_USERS": "maybe"})


def test_overrides_lists_only_known_keys_that_are_set():
    environ = {
        "PROD_MAX_USERS": "5",
        "PROD_STORAGE_BUCKET": "b",
        "PROD_SOMETHING_ELSE": "x",
        "OTHER_MAX_USERS": "9",
    }
    assert envvars.overrides("prod", environ) == {"MAX_USERS": "5", "STORAGE_BUCKET": "b"}


def test_names_that_would_read_the_wrong_variables_are_refused():
    assert envvars.name_problem("pawabase") and "reserved" in envvars.name_problem("pawabase")
    assert envvars.name_problem("PAWABASE")
    problem = envvars.name_problem("my_env", ["my-env", "production"])
    assert problem and "my-env" in problem
    assert envvars.name_problem("my-env", ["my-env"]) is None  # itself is fine
    assert envvars.name_problem("staging", ["production"]) is None


def test_typos_are_reported_but_unrelated_variables_are_not():
    environ = {
        "PRODUCTION_STORAGE_BUKET": "b",  # typo
        "PRODUCTION_MAX_USER": "5",  # typo
        "PRODUCTION_STORAGE_BUCKET": "ok",  # known
        "PRODUCTION_HOME": "/x",  # not a setting family: left alone
        "POSTGRES_PASSWORD": "pw",  # an environment called postgres, unrelated variable
        "PAWABASE_STORAGE_BUKET": "b",  # global names are the settings class's business
    }
    found = envvars.unrecognised(["production", "postgres"], environ)
    assert found == {"production": ["PRODUCTION_MAX_USER", "PRODUCTION_STORAGE_BUKET"]}


def test_a_longer_environment_name_owns_its_own_variables():
    environ = {"PROD_EU_STORAGE_BUKET": "b"}
    assert envvars.unrecognised(["prod", "prod-eu"], environ) == {
        "prod-eu": ["PROD_EU_STORAGE_BUKET"]
    }
