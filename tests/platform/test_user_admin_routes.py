"""Your users, administered with the environment's secret key, under the management plane."""

ENV = "/platform/v1/envs/development"


async def test_a_secret_key_manages_its_environments_users(api):
    created = await api.studio.post(
        f"{ENV}/auth/users",
        json={"email": "ada@example.com", "password": "Correct-horse-9", "name": "Ada"},
    )
    user_id = created["id"]
    listed = await api.studio.get(f"{ENV}/auth/users")
    assert [u["email"] for u in listed["data"]] == ["ada@example.com"]
    changed = await api.studio.patch(f"{ENV}/auth/users/{user_id}", json={"name": "Ada L."})
    assert changed["name"] == "Ada L."
    assert (await api.studio.get(f"{ENV}/auth/stats"))["users"] >= 1
    await api.studio.delete(f"{ENV}/auth/users/{user_id}")
    assert (await api.studio.get(f"{ENV}/auth/users"))["data"] == []


async def test_the_service_only_admin_api_stays_off_the_public_gateway(api):
    for path in ("/admin/v1/envs/development/users", "/admin/v1/users/x"):
        assert (
            await api.http.get(path, headers=api.context_headers("development", role="service"))
        ).status_code in (401, 403, 404)


async def test_a_secret_key_manages_only_its_own_environment(api):
    secret = api.context_headers("development", role="service")
    own = await api.http.get("/platform/v1/envs/development/auth/users", headers=secret)
    assert own.status_code == 200
    other = await api.http.get("/platform/v1/envs/staging/auth/users", headers=secret)
    assert other.status_code == 403
    anon = await api.http.get(
        "/platform/v1/envs/development/auth/users", headers=api.context_headers("development")
    )
    assert anon.status_code in (401, 403)
