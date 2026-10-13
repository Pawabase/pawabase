"""Projects, their one database, and environments inside them."""

import asyncio

import pytest

from pawabase_server.db.models import Environment, Project, ProjectDatabaseBinding
from pawabase_server.projects.manager import (
    ProjectError,
    ProjectManager,
    ensure_default_project,
)

ENV = "/platform/v1/envs/development"


def manager(api) -> ProjectManager:
    return ProjectManager(api.settings, api.platform.box)


def sqlite_url(tmp_path, name):
    return f"sqlite://{tmp_path}/{name}/{{env}}.db"


async def test_a_fresh_installation_has_a_default_project_with_its_environment(api):
    project = await Project.get(slug="default")
    assert project.is_default and project.status == "ready"
    binding = await ProjectDatabaseBinding.get(project_id=project.id)
    assert binding.status == "ready" and binding.mode == "local"
    environment = await Environment.get(name="development")
    assert environment.project_id == project.id and environment.slug == "development"
    assert environment.schema_name == "development"  # SQLite: the file's stem


async def test_starting_again_changes_nothing_even_when_several_processes_start_together(api):
    before = (
        await Project.all().count(),
        await Environment.all().count(),
        await ProjectDatabaseBinding.all().count(),
    )
    reports = await asyncio.gather(*(ensure_default_project(api.settings) for _ in range(5)))
    assert all(not r["created"] and not r["attached"] and not r["conflicts"] for r in reports)
    after = (
        await Project.all().count(),
        await Environment.all().count(),
        await ProjectDatabaseBinding.all().count(),
    )
    assert after == before


async def test_environments_from_before_projects_are_attached_with_the_schema_they_already_use(api):
    old = await Environment.create(name="staging", settings={"public_docs": False})
    assert old.project_id is None
    report = await ensure_default_project(api.settings)
    assert report["attached"] == ["staging"]
    old = await Environment.get(id=old.id)
    default = await Project.get(slug="default")
    assert (old.project_id, old.slug, old.schema_name) == (default.id, "staging", "staging")
    assert old.id == (await Environment.get(name="staging")).id  # the id did not change


async def test_a_different_default_database_does_not_silently_leave_existing_data_behind(
    api, monkeypatch, tmp_path
):
    await Environment.all().update(project_id=None, slug=None, schema_name=None)
    await ProjectDatabaseBinding.all().delete()
    monkeypatch.setenv("DEFAULT_DATABASE_URL", f"sqlite://{tmp_path}/elsewhere.db")
    report = await ensure_default_project(api.settings)
    assert report["conflicts"] and not report["attached"]
    assert await Environment.filter(project_id=None).count() >= 1  # untouched
    assert not await ProjectDatabaseBinding.exists()


async def test_a_project_has_one_database_and_environments_get_their_own_schema(api, tmp_path):
    m = manager(api)
    project, created = await m.create_project(name="Ecommerce")
    assert created and project.slug == "ecommerce" and project.status == "pending"
    again, created = await m.create_project(name="Ecommerce")
    assert not created and again.id == project.id

    with pytest.raises(ProjectError) as unbound:
        await m.create_environment("ecommerce", "production")
    assert unbound.value.code == "not_bound"

    bound = await m.bind_database("ecommerce", url=sqlite_url(tmp_path, "ecommerce"))
    assert bound["database"]["status"] == "ready"
    assert "sqlite" not in str(
        bound["database"]["reference"].get("address", "")
    )  # sealed, not shown
    production = await m.create_environment("ecommerce", "production")
    assert production["key"] == "ecommerce-production" and production["schema"] == "production"
    assert set(production["keys"]) == {"publishable", "secret"}
    assert production["status"] == "ready"

    # One primary database: a second one is refused unless the caller says so, and even then no data moves.
    with pytest.raises(ProjectError) as second:
        await m.bind_database("ecommerce", url=sqlite_url(tmp_path, "other"))
    assert second.value.code == "already_bound"
    unchanged = await m.bind_database("ecommerce", url=sqlite_url(tmp_path, "ecommerce"))
    assert unchanged.get("unchanged")


async def test_project_gateway_limits_are_versioned_and_validate_their_shape(api):
    m = manager(api)
    project, _ = await m.create_project(name="metered")
    original = project.limits_version
    updated = await m.update(
        "metered", limits={"daily_request_limit": 1000, "rate_limit": 20, "rate_window": 60}
    )
    assert updated["limits"] == {"daily_request_limit": 1000, "rate_limit": 20, "rate_window": 60}
    assert updated["limits_version"] == original + 1
    patched = await m.patch_limits(
        "metered", {"daily_request_limit": 0, "max_active_connections": 5}
    )
    assert patched["limits"] == {"rate_limit": 20, "rate_window": 60, "max_active_connections": 5}
    with pytest.raises(ProjectError):
        await m.update("metered", limits={"unknown": 1})


async def test_two_projects_can_each_have_a_production_without_seeing_each_other(api, tmp_path):
    m = manager(api)
    for name in ("shop", "crm"):
        await m.create_project(name=name)
        await m.bind_database(name, url=sqlite_url(tmp_path, name))
    shop = await m.create_environment("shop", "production")
    crm = await m.create_environment("crm", "production")
    assert shop["slug"] == crm["slug"] == "production" and shop["key"] != crm["key"]

    for env_key, title in ((shop["key"], "a book"), (crm["key"], "a client")):
        base = f"/platform/v1/envs/{env_key}"
        await api.studio.post(
            f"{base}/resources",
            json={
                "name": "items",
                "fields": [{"name": "title", "type": "string"}],
                "operations": {"list": {"enabled": True, "policy": "public"}},
            },
        )
        await api.studio.post(f"{base}/resources/items/schema/apply", json={})
        await api.studio.post(f"{base}/resources/items/records", json={"title": title})
    shop_rows = (await api.studio.get(f"/platform/v1/envs/{shop['key']}/resources/items/records"))[
        "data"
    ]
    crm_rows = (await api.studio.get(f"/platform/v1/envs/{crm['key']}/resources/items/records"))[
        "data"
    ]
    assert [r["title"] for r in shop_rows] == ["a book"]
    assert [r["title"] for r in crm_rows] == ["a client"]
    # Each project's data is in its own database.
    assert (tmp_path / "shop" / "production.db").exists() and (
        tmp_path / "crm" / "production.db"
    ).exists()


async def test_a_project_can_be_deleted_only_when_empty_and_the_default_never(api, tmp_path):
    m = manager(api)
    await m.create_project(name="scratch")
    await m.bind_database("scratch", url=sqlite_url(tmp_path, "scratch"))
    await m.create_environment("scratch", "dev", make_keys=False)
    with pytest.raises(ProjectError) as busy:
        await m.delete("scratch")
    assert busy.value.code == "not_empty"
    with pytest.raises(ProjectError) as protected:
        await m.delete("default")
    assert protected.value.code == "protected"
    await Environment.filter(name="scratch-dev").delete()
    assert (await m.delete("scratch"))["deleted"] == "scratch"


async def test_credentials_are_never_shown_and_can_be_rotated(api, tmp_path):
    m = manager(api)
    await m.create_project(name="vault")
    url = f"sqlite://{tmp_path}/vault/{{env}}.db"
    await m.bind_database("vault", url=url)
    shown = str(await m.show("vault"))
    assert str(tmp_path) not in shown
    rotated = await m.rotate_credentials("vault", url=f"sqlite://{tmp_path}/vault/{{env}}.db?x=1")
    assert rotated["database"]["generation"] == 2
    with pytest.raises(ProjectError):
        await m.rotate_credentials("vault", url="postgres://u:p@127.0.0.1:1/none")  # another kind
    # A wrong address is refused and nothing is bound.
    await m.create_project(name="broken")
    with pytest.raises(ProjectError) as bad:
        await m.bind_database("broken", url="postgres://u:secret@127.0.0.1:1/none")
    assert bad.value.code == "unreachable" and "secret" not in str(bad.value)
    assert not await ProjectDatabaseBinding.filter(project__slug="broken").exists()


async def test_a_binding_through_an_environment_variable_keeps_the_address_out_of_the_database(
    api, tmp_path, monkeypatch
):
    monkeypatch.setenv("SHOP_DATABASE_URL", f"sqlite://{tmp_path}/shop/{{env}}.db")
    m = manager(api)
    await m.create_project(name="shop")
    bound = await m.bind_database("shop", url_env="SHOP_DATABASE_URL")
    stored = (await ProjectDatabaseBinding.get(project__slug="shop")).connection_ref
    assert stored == "env:SHOP_DATABASE_URL" and str(tmp_path) not in stored
    assert bound["database"]["reference"]["variable"] == "SHOP_DATABASE_URL"
    monkeypatch.delenv("SHOP_DATABASE_URL")
    problem = await m.test_database("shop")
    assert problem["ok"] is False and "not set" in problem["error"]


def test_the_package_installer_is_not_handed_the_servers_secrets(monkeypatch):
    from pawabase_server.functions.deployments import Deployments

    monkeypatch.setenv("PAWABASE_MASTER_KEY", "super-secret-master-key-0123456789abcdef")
    monkeypatch.setenv("PAWABASE_DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("PIP_INDEX_URL", "https://index.test/simple")
    environment = Deployments._install_environment()
    assert "PAWABASE_MASTER_KEY" not in environment and "PAWABASE_DATABASE_URL" not in environment
    assert environment["PIP_INDEX_URL"] == "https://index.test/simple" and "PATH" in environment


async def test_a_namespace_keeps_runtimes_apart_on_a_shared_redis(settings):
    from pawabase_server.core import namespace

    namespace.configure("runtime-a")
    try:
        assert namespace.scoped("pawabase.events") == "runtime-a:pawabase.events"
    finally:
        namespace.configure("")
    assert namespace.scoped("pawabase.events") == "pawabase.events"


async def test_an_environment_made_through_the_management_api_belongs_to_the_default_project(api):
    made = await api.studio.post("/platform/v1/envs", json={"name": "qa"})
    assert made["name"] == "qa"
    environment = await Environment.get(name="qa")
    default = await Project.get(slug="default")
    assert (environment.project_id, environment.slug, environment.schema_name) == (
        default.id,
        "qa",
        "qa",
    )
