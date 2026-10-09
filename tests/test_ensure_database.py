from pawabase_core.ensure_database import connect_args


def test_ssl_leaves_the_url_and_becomes_a_connect_option():
    url, options = connect_args(
        "postgres://u:p@h:6432/db?ssl=require&minsize=0&maxsize=3&statement_cache_size=0&application_name=x"
    )
    assert options == {"ssl": "require"}
    assert (
        url == "postgres://u:p@h:6432/db?application_name=x"
    )  # nothing PgBouncer would refuse as a startup parameter is left


def test_a_plain_url_is_unchanged():
    assert connect_args("postgres://u:p@h:5432/db") == ("postgres://u:p@h:5432/db", {})


def test_sslmode_is_understood_too():
    assert connect_args("postgres://u:p@h/db?sslmode=disable")[1] == {"ssl": "disable"}
