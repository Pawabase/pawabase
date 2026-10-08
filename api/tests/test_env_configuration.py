"""Configuration comes from environment variables: `<ENV>_<KEY>` first, `PAWABASE_<KEY>` second.

Older installs kept database and storage settings on the environment (`infra`). Those keep
working, deprecated, and an environment variable beats them. Mail is the one exception: it
is read from the environment only.
"""

import logging

import pytest

ENV = "/platform/v1/envs/development"


async def state_of(api, env="development"):
    api.platform.envs.forget(env)
    return await api.platform.state(env)


# ── data ─────────────────────────────────────────────────────────────────


async def test_the_default_data_database_is_one_per_environment(api):
    state = await state_of(api)
    assert state.database_url().endswith("/development.db")


async def test_an_environment_variable_sets_the_data_database(api, monkeypatch, tmp_path):
    monkeypatch.setenv("DEVELOPMENT_DATA_URL", f"sqlite://{tmp_path}/own.db")
    assert (await state_of(api)).database_url() == f"sqlite://{tmp_path}/own.db"


async def test_legacy_infra_database_url_still_works_and_is_deprecated(api, tmp_path, caplog):
    await api.studio.patch(ENV, json={"infra": {"database_url": f"sqlite://{tmp_path}/legacy.db"}})
    with caplog.at_level(logging.WARNING, logger="pawabase.api"):
        url = (await state_of(api)).database_url()
    assert url == f"sqlite://{tmp_path}/legacy.db"
    assert "infra.database_url is deprecated" in caplog.text
    assert "DEVELOPMENT_DATA_URL" in caplog.text


async def test_an_environment_variable_beats_legacy_infra(api, monkeypatch, tmp_path):
    await api.studio.patch(ENV, json={"infra": {"database_url": f"sqlite://{tmp_path}/legacy.db"}})
    monkeypatch.setenv("DEVELOPMENT_DATA_URL", f"sqlite://{tmp_path}/new.db")
    assert (await state_of(api)).database_url() == f"sqlite://{tmp_path}/new.db"


# ── storage ──────────────────────────────────────────────────────────────


async def test_storage_defaults_to_the_platform_default(api):
    config = api.platform.storage._config(await state_of(api))
    assert config["driver"] == "local"


async def test_storage_variables_are_laid_over_the_default(api, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_STORAGE_ENDPOINT", "http://minio.internal:9000")
    monkeypatch.setenv("DEVELOPMENT_STORAGE_BUCKET", "acme")
    monkeypatch.setenv("DEVELOPMENT_STORAGE_ACCESS_KEY", "ak")
    monkeypatch.setenv("DEVELOPMENT_STORAGE_SECRET_KEY", "sk")
    monkeypatch.setenv("DEVELOPMENT_STORAGE_PATH_STYLE", "false")
    config = api.platform.storage._config(await state_of(api))
    assert config["driver"] == "s3"  # an endpoint on a local-by-default deployment means S3
    assert config["bucket"] == "acme"
    assert config["endpoint"] == "http://minio.internal:9000"
    assert config["path_style"] is False
    assert config.get("region", "us-east-1") == "us-east-1"  # what the driver uses when it is unset


async def test_one_storage_variable_changes_only_that_setting(api, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_STORAGE_PREFIX", "tenants/acme")
    config = api.platform.storage._config(await state_of(api))
    assert config["driver"] == "local" and config["prefix"] == "tenants/acme"


async def test_legacy_infra_storage_still_works_and_variables_win_over_it(api, monkeypatch, caplog):
    await api.studio.patch(
        ENV, json={"infra": {"storage": {"endpoint": "http://old:9000", "bucket": "old"}}}
    )
    with caplog.at_level(logging.WARNING, logger="pawabase.api"):
        config = api.platform.storage._config(await state_of(api))
    assert config["driver"] == "s3" and config["bucket"] == "old"
    assert "infra.storage is deprecated" in caplog.text
    monkeypatch.setenv("DEVELOPMENT_STORAGE_BUCKET", "new")
    config = api.platform.storage._config(await state_of(api))
    assert config["bucket"] == "new" and config["endpoint"] == "http://old:9000"


# ── mail ─────────────────────────────────────────────────────────────────


async def test_mail_is_suppressed_until_a_host_is_configured(api):
    config = api.platform.mail._config(await state_of(api))
    assert config.suppress_send is True


async def test_mail_comes_from_the_environment_variables(api, monkeypatch):
    monkeypatch.setenv("PAWABASE_MAIL_HOST", "smtp.global.example")
    monkeypatch.setenv("PAWABASE_MAIL_FROM", "hello@global.example")
    api.platform.settings.mail_host = "smtp.global.example"
    api.platform.settings.mail_from = "hello@global.example"
    config = api.platform.mail._config(await state_of(api))
    assert (config.smtp_host, config.default_from, config.suppress_send) == (
        "smtp.global.example",
        "hello@global.example",
        False,
    )
    assert config.smtp_port == 587 and config.use_tls is True and config.use_ssl is False


async def test_an_environments_mail_variables_beat_the_global_ones(api, monkeypatch):
    api.platform.settings.mail_host = "smtp.global.example"
    monkeypatch.setenv("DEVELOPMENT_MAIL_HOST", "smtp.dev.example")
    monkeypatch.setenv("DEVELOPMENT_MAIL_PORT", "465")
    monkeypatch.setenv("DEVELOPMENT_MAIL_USERNAME", "dev-user")
    monkeypatch.setenv("DEVELOPMENT_MAIL_PASSWORD", "dev-pass")
    config = api.platform.mail._config(await state_of(api))
    assert config.smtp_host == "smtp.dev.example" and config.smtp_port == 465
    assert config.use_ssl is True and config.use_tls is False
    assert (config.smtp_username, config.smtp_password) == ("dev-user", "dev-pass")


async def test_mail_suppress_stops_sending_even_with_a_host(api, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MAIL_HOST", "smtp.dev.example")
    monkeypatch.setenv("DEVELOPMENT_MAIL_SUPPRESS", "true")
    assert api.platform.mail._config(await state_of(api)).suppress_send is True


async def test_a_password_can_name_one_of_the_environments_secrets(api, monkeypatch):
    await api.studio.put(f"{ENV}/secrets/SMTP_PASSWORD", json={"value": "from-the-vault"})
    monkeypatch.setenv("DEVELOPMENT_MAIL_HOST", "smtp.dev.example")
    monkeypatch.setenv("DEVELOPMENT_MAIL_PASSWORD", "secret://SMTP_PASSWORD")
    assert api.platform.mail._config(await state_of(api)).smtp_password == "from-the-vault"


async def test_old_infra_mail_is_ignored_and_says_so(api, caplog):
    """The one deliberate break: mail configuration stored on the environment is not read."""
    await api.studio.patch(
        ENV, json={"infra": {"database_url": ""}}
    )  # a harmless key: infra exists
    environment = await __import__("database.models", fromlist=["Environment"]).Environment.get(
        name="development"
    )
    environment.infra = {"mail": {"host": "smtp.stored.example", "from": "old@example.com"}}
    await environment.save()
    with caplog.at_level(logging.WARNING, logger="pawabase.api"):
        config = api.platform.mail._config(await state_of(api))
    assert config.suppress_send is True and config.smtp_host == "localhost"
    assert "infra.mail is no longer read" in caplog.text
    assert "PAWABASE_MAIL_*" in caplog.text


# ── limits ───────────────────────────────────────────────────────────────


async def test_a_limit_is_the_environments_own_or_the_deployments(api, monkeypatch):
    assert api.platform.limit("development", "MAX_USERS", 7) == 7
    monkeypatch.setenv("DEVELOPMENT_MAX_USERS", "3")
    assert api.platform.limit("development", "MAX_USERS", 7) == 3
    assert api.platform.limit("staging", "MAX_USERS", 7) == 7


async def test_usage_reports_the_limits_an_environment_runs_under(api, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MAX_USERS", "12")
    monkeypatch.setenv("DEVELOPMENT_MAX_UPLOAD_BYTES", "2048")
    monkeypatch.setenv("DEVELOPMENT_MAX_API_KEYS_PER_ENVIRONMENT", "4")
    own = await api.studio.get("/platform/v1/usage", params={"env": "development"})
    assert (own["users"]["limit"], own["uploads"]["limit"], own["api_keys"]["limit"]) == (
        12,
        2048,
        4,
    )
    await api.studio.post(f"{ENV}/keys", json={"name": "counted", "role": "secret"})
    counted = await api.studio.get("/platform/v1/usage", params={"env": "development"})
    assert counted["api_keys"]["used"] >= 1  # the count was always 0 before `env` was read properly
    overall = await api.studio.get("/platform/v1/usage")
    assert overall["users"]["limit"] == api.platform.settings.max_users
    assert overall["uploads"]["limit"] == api.platform.settings.max_upload_bytes


async def test_the_key_limit_applies_per_environment(api, monkeypatch):
    from pawabase_core.clients import ServiceError

    monkeypatch.setenv("DEVELOPMENT_MAX_API_KEYS_PER_ENVIRONMENT", "1")
    first = await api.studio.post(f"{ENV}/keys", json={"name": "one", "role": "secret"})
    assert first["key"]
    with pytest.raises(ServiceError) as caught:
        await api.studio.post(f"{ENV}/keys", json={"name": "two", "role": "secret"})
    assert caught.value.status in (403, 409, 422)


# ── moving old settings over ─────────────────────────────────────────────


def test_export_prints_what_an_old_install_stored_as_variables():
    from app.export_config import render

    lines = render(
        [
            (
                "production",
                {
                    "database_url": "postgres://u:p@db/prod",
                    "storage": {
                        "endpoint": "http://minio:9000",
                        "bucket": "acme prod",
                        "access_key": "AK",
                        "path_style": True,
                        "unknown_field": "skipped",
                    },
                    "mail": {
                        "host": "smtp.example.com",
                        "port": 465,
                        "password": "secret://MAIL_SMTP_PASSWORD",
                        "use_ssl": True,
                        "from": "Acme <hi@acme.test>",
                    },
                },
            ),
            ("staging", {}),
            ("my-env", {"database_url": "sqlite:///x.db"}),
        ]
    )
    assert lines == [
        "# environment: production",
        "PRODUCTION_DATA_URL=postgres://u:p@db/prod",
        "PRODUCTION_STORAGE_ENDPOINT=http://minio:9000",
        'PRODUCTION_STORAGE_BUCKET="acme prod"',
        "PRODUCTION_STORAGE_ACCESS_KEY=AK",
        "PRODUCTION_STORAGE_PATH_STYLE=true",
        "PRODUCTION_MAIL_HOST=smtp.example.com",
        "PRODUCTION_MAIL_PORT=465",
        "PRODUCTION_MAIL_PASSWORD=secret://MAIL_SMTP_PASSWORD",
        "PRODUCTION_MAIL_USE_SSL=true",
        'PRODUCTION_MAIL_FROM="Acme <hi@acme.test>"',
        "",
        "# environment: my-env",
        "MY_ENV_DATA_URL=sqlite:///x.db",
        "",
    ]


async def test_the_exported_variables_configure_the_environment_the_same_way(api, monkeypatch):
    """What `export_config` prints is exactly what the platform reads back."""
    from app.export_config import render

    old = {"mail": {"host": "smtp.old.example", "port": 465, "from": "old@example.com"}}
    for line in render([("development", old)]):
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            monkeypatch.setenv(name, value.strip('"'))
    config = api.platform.mail._config(await state_of(api))
    assert (config.smtp_host, config.smtp_port, config.default_from) == (
        "smtp.old.example",
        465,
        "old@example.com",
    )


# ── what the API accepts ─────────────────────────────────────────────────


async def test_storing_mail_on_an_environment_is_refused_with_the_way_forward(api):
    from pawabase_core.clients import ServiceError

    with pytest.raises(ServiceError) as caught:
        await api.studio.patch(ENV, json={"infra": {"mail": {"host": "smtp.example.com"}}})
    assert caught.value.status == 422
    assert "PAWABASE_MAIL_*" in str(caught.value.body)


async def test_database_and_storage_infra_are_still_accepted_and_flagged(api, tmp_path):
    saved = await api.studio.patch(
        ENV, json={"infra": {"database_url": f"sqlite://{tmp_path}/x.db"}}
    )
    assert saved["infra"]["database_url"].endswith("x.db")
    assert saved["deprecations"] == ["infra.database_url is deprecated: set DEVELOPMENT_DATA_URL"]


async def test_mail_left_by_an_older_install_can_be_cleared(api):
    from database.models import Environment

    environment = await Environment.get(name="development")
    environment.infra = {"mail": {"host": "smtp.stored.example"}}
    await environment.save()
    view = await api.studio.get(ENV)
    assert any("infra.mail is ignored" in note for note in view["deprecations"])
    cleared = await api.studio.patch(ENV, json={"infra": {"mail": None}})
    assert cleared["infra"].get("mail") is None and cleared["deprecations"] == []


async def test_a_name_that_would_read_another_environments_variables_is_refused(api):
    from pawabase_core.clients import ServiceError

    await api.studio.post("/platform/v1/envs", json={"name": "my-env"})
    for name in ("my_env", "pawabase"):
        with pytest.raises(ServiceError) as caught:
            await api.studio.post("/platform/v1/envs", json={"name": name})
        assert caught.value.status == 422, name
    ok = await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    assert ok["name"] == "staging"


async def test_startup_audit_reports_leftovers_and_typos(api, monkeypatch, caplog):
    from database.models import Environment

    environment = await Environment.get(name="development")
    environment.infra = {"database_url": "sqlite:///x.db", "mail": {"host": "h"}}
    await environment.save()
    monkeypatch.setenv("DEVELOPMENT_STORAGE_BUKET", "typo")
    api.platform._deprecations.clear()
    with caplog.at_level(logging.WARNING, logger="pawabase.api"):
        await api.platform.audit_configuration()
    assert "infra.database_url is deprecated" in caplog.text
    assert "infra.mail is no longer read" in caplog.text
    assert "DEVELOPMENT_STORAGE_BUKET" in caplog.text
