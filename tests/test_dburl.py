from pawabase_core.dburl import pooled


def test_pool_limits_are_added_to_a_postgres_url():
    url = pooled("postgres://u:p@db:5432/app", minimum=0, maximum=3, statement_cache=0)
    assert url == "postgres://u:p@db:5432/app?minsize=0&maxsize=3&statement_cache_size=0"


def test_existing_query_parameters_are_kept_and_never_overridden():
    url = pooled("postgres://u:p@db/app?schema=pb_x&maxsize=9", minimum=0, maximum=3)
    assert "schema=pb_x" in url and "maxsize=9" in url and "maxsize=3" not in url
    assert "minsize=0" in url


def test_unset_changes_nothing():
    for url in ("postgres://u:p@db/app", "sqlite:///x.db", "mysql://u:p@h/db"):
        assert pooled(url) == url


def test_only_postgres_urls_are_touched():
    assert pooled("sqlite:///x.db", minimum=0, maximum=3) == "sqlite:///x.db"
    assert pooled("mysql://u:p@h/db", minimum=0, maximum=3) == "mysql://u:p@h/db"
    assert pooled("postgresql://u:p@h/db", maximum=2).endswith("?maxsize=2")


def test_settings_apply_them():
    from pawabase_core.settings import PlatformSettings

    settings = PlatformSettings(_env_file=None, db_pool_min=0, db_pool_max=3)
    assert settings.tuned("postgres://u:p@h/db") == "postgres://u:p@h/db?minsize=0&maxsize=3"
    assert PlatformSettings(_env_file=None).tuned("postgres://u:p@h/db") == "postgres://u:p@h/db"
