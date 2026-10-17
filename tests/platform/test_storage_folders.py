"""Empty folders in a bucket: created and removed from Studio, backed by a marker object."""

import pytest

ENV = "/platform/v1/envs/development"


@pytest.fixture
async def store(api):
    await api.studio.post(
        f"{ENV}/buckets",
        json={
            "name": "media",
            "public": True,
            "write_policy": "authenticated",
            "list_policy": "authenticated",
        },
    )
    return api


async def listing(api, prefix=""):
    return await api.studio.get(f"{ENV}/buckets/media/objects", params={"prefix": prefix})


async def folder(api, prefix):
    return await api.studio.post(f"{ENV}/buckets/media/folders", json={"prefix": prefix})


async def put_object(api, key, data=b"x", content_type="text/plain"):
    raw = await api.studio.request_raw(
        "PUT",
        f"{ENV}/buckets/media/object",
        content=data,
        params={"key": key},
        headers={"content-type": content_type},
    )
    assert raw["status"] == 200, raw["body"]
    return raw["body"]


async def remove_folder(api, prefix):
    return await api.studio.request_raw("DELETE", f"{ENV}/buckets/media/folders/{prefix}")


async def test_a_new_folder_shows_as_a_prefix(store):
    made = await folder(store, "docs")
    assert made["prefix"] == "docs/" and made["key"] == "docs/pawabase.folder"
    root = await listing(store)
    assert root["prefixes"] == ["docs/"]
    assert root["files"] == []


async def test_folders_nest_and_appear_under_their_parent(store):
    await folder(store, "docs")
    await folder(store, "docs/reports")
    inside = await listing(store, "docs/")
    assert inside["prefixes"] == ["docs/reports/"]


async def test_creating_a_folder_twice_is_harmless(store):
    await folder(store, "docs")
    await folder(store, "docs")
    assert (await listing(store))["prefixes"] == ["docs/"]


async def test_a_folder_cannot_shadow_an_object_of_the_same_name(store):
    await put_object(store, "photos")
    clash = await store.studio.request_raw(
        "POST", f"{ENV}/buckets/media/folders", json={"prefix": "photos"}
    )
    assert clash["status"] == 409


async def test_an_empty_folder_can_be_deleted(store):
    await folder(store, "docs")
    gone = await remove_folder(store, "docs")
    assert gone["status"] == 204
    assert (await listing(store))["prefixes"] == []


async def test_a_folder_holding_a_file_is_not_deletable(store):
    await folder(store, "docs")
    await put_object(store, "docs/a.txt", b"hello")
    refused = await remove_folder(store, "docs")
    assert refused["status"] == 409
    assert "docs/" in (await listing(store))["prefixes"]


async def test_a_folder_holding_only_a_subfolder_is_not_deletable(store):
    await folder(store, "docs")
    await folder(store, "docs/inner")
    refused = await remove_folder(store, "docs")
    assert refused["status"] == 409


async def test_deleting_a_folder_that_is_not_there_is_not_found(store):
    assert (await remove_folder(store, "nope"))["status"] == 404


async def test_the_marker_is_never_exposed(store):
    await folder(store, "docs")
    root = await listing(store)
    assert all("pawabase.folder" not in f["key"] for f in root["files"])
    details = await store.studio.request_raw(
        "GET", f"{ENV}/buckets/media/object", params={"key": "docs/pawabase.folder"}
    )
    assert details["status"] == 404
    public = await store.http.get(
        "/storage/v1/list/media", headers=store.user_headers("development", user_id="7")
    )
    assert public.status_code == 200
    body = public.json()
    assert "docs/" in body["prefixes"]
    assert all("pawabase.folder" not in f["key"] for f in body["files"])
    served = await store.http.get("/storage/v1/files/development/media/docs/pawabase.folder")
    assert served.status_code == 404
    signed = await store.http.post(
        "/storage/v1/sign/media/docs/pawabase.folder",
        json={"method": "GET"},
        headers=store.user_headers("development", user_id="7"),
    )
    assert signed.status_code == 404
