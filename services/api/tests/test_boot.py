async def test_boot_and_projects(api):
    assert (await api.http.get("/health")).status_code == 200
    assert (await api.http.get("/platform/v1/projects")).status_code == 401
    created = await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
    assert set(created["keys"]) == {"development", "production"}
    listing = await api.studio.get("/platform/v1/projects")
    assert listing["data"][0]["ref"] == "acme"
    policy = await api.studio.post("/platform/v1/projects/acme/envs/development/policies", json={"name": "editors", "condition": {"role": "editor"}})
    assert policy["name"] == "editors"
