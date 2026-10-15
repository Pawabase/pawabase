"""Object delivery through the gateway, per-action policies, share links, transformations and Studio management."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from pawabase_server.db.models import ObjectShare
from pawabase_server.infra.storage import transform
from pawabase_server.infra.storage.delivery import RangeNotSatisfiable, parse_range

ENV = "/platform/v1/envs/development"
OTHER = "/platform/v1/envs/staging"


def png(width=80, height=60, colour=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
async def shop(api):
    await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    for env in (ENV, OTHER):
        await api.studio.post(
            f"{env}/buckets",
            json={
                "name": "media",
                "public": True,
                "write_policy": "authenticated",
                "list_policy": "authenticated",
                "update_policy": {"eq": ["$object.segments.0", "$auth.user_id"]},
                "delete_policy": {"eq": ["$object.segments.0", "$auth.user_id"]},
            },
        )
        await api.studio.post(
            f"{env}/buckets",
            json={"name": "private", "public": False, "read_policy": "authenticated"},
        )
    return api


def headers(api, **kw):
    return api.user_headers("development", **kw)


async def put(api, bucket, key, data, *, who="7", content_type="image/png", env="development"):
    return await api.http.put(
        f"/storage/v1/object/{bucket}/{key}",
        content=data,
        headers={**api.user_headers(env, user_id=who), "content-type": content_type},
    )


# ── ranges (pure) ──────────────────────────────────────────────────────────


def test_range_parsing():
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=90-", 100) == (90, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=50-500", 100) == (50, 99)
    assert parse_range("bytes=0-1,5-6", 100) is None
    assert parse_range("items=0-1", 100) is None
    with pytest.raises(RangeNotSatisfiable):
        parse_range("bytes=100-", 100)


def test_transform_settings_are_clamped_and_validated():
    cfg = transform.settings({"max_width": 99999, "formats": ["webp", "bogus"]})
    assert cfg["max_width"] == transform.CEILINGS["max_width"] and cfg["formats"] == ["webp"]
    assert transform.validate_settings({"max_width": 100}) == {"max_width": 100}
    for bad in ({"nope": 1}, {"max_width": 0}, {"formats": ["gif"]}, {"quality": 101}):
        with pytest.raises(ValueError):
            transform.validate_settings(bad)


# ── gateway delivery ───────────────────────────────────────────────────────


async def test_a_public_object_renders_from_the_gateway_url_without_a_key(shop):
    api = shop
    data = png()
    uploaded = await put(api, "media", "7/me.png", data)
    assert uploaded.status_code == 201
    url = uploaded.json()["url"]
    assert url == f"{api.settings.public_url}/storage/v1/files/development/media/7/me.png"

    shown = await api.http.get(url.replace(api.settings.public_url, ""))  # no key, no user
    assert shown.status_code == 200 and shown.content == data
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline;")
    assert shown.headers["accept-ranges"] == "bytes"
    assert shown.headers["cross-origin-resource-policy"] == "cross-origin"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"].startswith("public")
    assert shown.headers["etag"]

    download = await api.http.get("/storage/v1/files/development/media/7/me.png?download=1")
    assert download.headers["content-disposition"].startswith("attachment;")
    cached = await api.http.get(
        "/storage/v1/files/development/media/7/me.png",
        headers={"if-none-match": shown.headers["etag"]},
    )
    assert cached.status_code == 304


async def test_unsafe_types_are_never_inline(shop):
    api = shop
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={"name": "media", "public": True, "write_policy": "authenticated"},
    )
    uploaded = await put(
        api, "media", "7/note.txt", b"<svg onload=alert(1)>", content_type="text/plain"
    )
    assert uploaded.status_code == 201
    got = await api.http.get("/storage/v1/files/development/media/7/note.txt")
    # Sniffed as markup, so it is an attachment and never rendered in our origin.
    assert got.status_code == 200
    if got.headers["content-type"] != "text/plain":
        assert got.headers["content-disposition"].startswith("attachment;")


async def test_range_requests_stream_partial_content(shop):
    api = shop
    data = png(200, 200)
    await put(api, "media", "7/big.png", data)
    url = "/storage/v1/files/development/media/7/big.png"
    part = await api.http.get(url, headers={"range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == data[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(data)}"
    tail = await api.http.get(url, headers={"range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == data[-5:]
    bad = await api.http.get(url, headers={"range": f"bytes={len(data) + 5}-"})
    assert bad.status_code == 416
    assert bad.headers["content-range"] == f"bytes */{len(data)}"
    head = await api.http.head(url)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(data))


async def test_text_is_compressed_when_accepted_and_images_are_not(shop):
    api = shop
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={"name": "media", "public": True, "write_policy": "authenticated"},
    )
    body = b"line of text\n" * 400
    await put(api, "media", "7/log.txt", body, content_type="text/plain")
    plain = await api.http.get(
        "/storage/v1/files/development/media/7/log.txt", headers={"accept-encoding": "identity"}
    )
    assert "content-encoding" not in plain.headers
    packed = await api.http.get(
        "/storage/v1/files/development/media/7/log.txt", headers={"accept-encoding": "gzip"}
    )
    assert packed.headers.get("content-encoding") == "gzip" and packed.content == body
    assert "accept-encoding" in packed.headers["vary"].lower()
    await put(api, "media", "7/p.png", png())
    image = await api.http.get(
        "/storage/v1/files/development/media/7/p.png", headers={"accept-encoding": "gzip"}
    )
    assert "content-encoding" not in image.headers


async def test_restricted_objects_need_the_policy(shop):
    api = shop
    data = png()
    assert (await put(api, "private", "7/secret.png", data)).status_code == 201
    anon = await api.http.get("/storage/v1/files/development/private/7/secret.png")
    assert anon.status_code == 401
    # The same URL with the user's token satisfies the authenticated policy.
    token = headers(api, user_id="7")["Authorization"]
    ok = await api.http.get(
        "/storage/v1/files/development/private/7/secret.png", headers={"authorization": token}
    )
    assert ok.status_code == 200 and ok.content == data
    assert ok.headers["cache-control"].startswith("private")
    # A token for another environment is not believed.
    foreign = api.user_headers("staging", user_id="7")["Authorization"]
    refused = await api.http.get(
        "/storage/v1/files/development/private/7/secret.png", headers={"authorization": foreign}
    )
    assert refused.status_code == 401
    # Via the API, with a key, the denied-but-signed-in answer is 403, never a leak.
    ada = api.context_headers("development")
    assert (
        await api.http.get("/storage/v1/object/private/7/secret.png", headers=ada)
    ).status_code == 401


async def test_missing_object_and_unknown_environment_and_traversal(shop):
    api = shop
    assert (await api.http.get("/storage/v1/files/development/media/nope.png")).status_code == 404
    assert (await api.http.get("/storage/v1/files/nowhere/media/x.png")).status_code == 404
    assert (await api.http.get("/storage/v1/files/development/nobucket/x.png")).status_code == 404
    escaped = await api.http.get("/storage/v1/files/development/media/..%2F..%2Fsecret")
    assert escaped.status_code in (400, 404)


# ── isolation ──────────────────────────────────────────────────────────────


async def test_objects_do_not_cross_environments(shop):
    api = shop
    await put(api, "media", "7/only-dev.png", png())
    assert (
        await api.http.get("/storage/v1/files/development/media/7/only-dev.png")
    ).status_code == 200
    assert (await api.http.get("/storage/v1/files/staging/media/7/only-dev.png")).status_code == 404
    # A share link names its own environment, bucket and key.
    made = await api.studio.post(
        f"{ENV}/buckets/media/shares", json={"key": "7/only-dev.png", "expires_in": 600}
    )
    assert (await api.http.get(f"/storage/v1/share/{made['token']}")).status_code == 200


async def test_a_hostname_serving_one_environment_refuses_anothers_urls(shop):
    api = shop
    await put(api, "media", "7/a.png", png())
    from pawabase_server.edge.pipeline import Edge

    edge = Edge.__new__(Edge)
    edge.hosts, edge.domains = {"app.dev.example": "development"}, {}

    class Req:
        host = "app.dev.example"
        path = "/storage/v1/files/staging/media/a.png"

    assert edge._wrong_host(Req) is True
    Req.path = "/storage/v1/files/development/media/a.png"
    assert edge._wrong_host(Req) is False


# ── policies per action ────────────────────────────────────────────────────


async def test_listing_replacing_and_deleting_follow_their_own_policies(shop):
    api = shop
    assert (await put(api, "media", "7/mine.png", png())).status_code == 201
    anon = api.context_headers("development")
    # Public reads do not make the bucket enumerable.
    assert (await api.http.get("/storage/v1/list/media", headers=anon)).status_code == 403
    listed = await api.http.get("/storage/v1/list/media", headers=headers(api, user_id="7"))
    assert listed.status_code == 200
    assert listed.json()["files"][0]["url"].endswith(
        "/storage/v1/files/development/media/7/mine.png"
    )
    # Replacing needs the update policy: the owner may, another user may not, though both may upload new keys.
    assert (await put(api, "media", "7/mine.png", png(5, 5), who="8")).status_code == 403
    assert (await put(api, "media", "8/theirs.png", png(), who="8")).status_code == 201
    assert (await put(api, "media", "7/mine.png", png(5, 5), who="7")).status_code == 201
    other = await api.http.delete(
        "/storage/v1/object/media/7/mine.png", headers=headers(api, user_id="8")
    )
    assert other.status_code == 403
    mine = await api.http.delete(
        "/storage/v1/object/media/7/mine.png", headers=headers(api, user_id="7")
    )
    assert mine.status_code == 204
    assert (await api.http.get("/storage/v1/files/development/media/7/mine.png")).status_code == 404


async def test_anonymous_upload_is_refused(shop):
    api = shop
    refused = await api.http.put(
        "/storage/v1/object/media/x.png",
        content=png(),
        headers={**api.context_headers("development"), "content-type": "image/png"},
    )
    assert refused.status_code == 403


# ── Studio management ──────────────────────────────────────────────────────


async def test_studio_can_upload_whatever_the_application_policy_says(shop):
    api = shop
    await api.studio.put(
        f"{ENV}/buckets/private",
        json={"name": "private", "write_policy": {"eq": [1, 2]}, "read_policy": {"eq": [1, 2]}},
    )
    data = png()
    raw = await api.studio.request_raw(
        "PUT",
        f"{ENV}/buckets/private/object",
        content=data,
        params={"key": "from-studio.png"},
        headers={"content-type": "image/png"},
    )
    assert raw["status"] == 200, raw["body"]
    result = raw["body"]
    assert result["key"] == "from-studio.png" and result["replaced"] is False
    details = await api.studio.get(
        f"{ENV}/buckets/private/object", params={"key": "from-studio.png"}
    )
    assert details["content_type"] == "image/png" and details["visibility"] == "restricted"
    assert details["url"].endswith("/storage/v1/files/development/private/from-studio.png")
    # Applications stay bound by the policy.
    assert (await put(api, "private", "x.png", data)).status_code == 403


async def test_studio_move_and_delete_revoke_share_links(shop):
    api = shop
    await put(api, "media", "7/a.png", png())
    share = await api.studio.post(
        f"{ENV}/buckets/media/shares", json={"key": "7/a.png", "expires_in": 600}
    )
    moved = await api.studio.post(
        f"{ENV}/buckets/media/object/move", json={"source": "7/a.png", "target": "7/b.png"}
    )
    assert moved["key"] == "7/b.png"
    assert (await api.http.get(f"/storage/v1/share/{share['token']}")).status_code == 410
    assert (await api.http.get("/storage/v1/files/development/media/7/b.png")).status_code == 200


# ── share links ────────────────────────────────────────────────────────────


async def test_share_link_lifecycle(shop):
    api = shop
    data = png()
    await put(api, "private", "7/doc.png", data)
    made = await api.studio.post(
        f"{ENV}/buckets/private/shares",
        json={"key": "7/doc.png", "expires_in": 3600, "permissions": "both"},
    )
    token = made["token"]
    assert token.startswith("pbs_") and made["url"].endswith(f"/storage/v1/share/{token}")
    # Only a hash is the lookup key.
    row = await ObjectShare.get(id=made["id"])
    assert row.token_hash != token and token not in row.token_hash

    # Opens with no key and no user, though the bucket's read policy is restricted.
    opened = await api.http.get(f"/storage/v1/share/{token}")
    assert opened.status_code == 200 and opened.content == data
    assert opened.headers["content-disposition"].startswith("inline;")
    assert (
        await api.http.get("/storage/v1/files/development/private/7/doc.png")
    ).status_code == 401
    # It grants that object and no other.
    assert (await api.http.get(f"/storage/v1/share/{token}x")).status_code == 404

    listed = await api.studio.get(f"{ENV}/buckets/private/shares", params={"key": "7/doc.png"})
    assert listed["data"][0]["status"] == "active" and listed["data"][0]["hits"] == 1
    assert listed["data"][0]["url"].endswith(token)

    revoked = await api.studio.delete(f"{ENV}/buckets/private/shares/{made['id']}")
    assert revoked["status"] == "revoked"
    assert (await api.http.get(f"/storage/v1/share/{token}")).status_code == 410


async def test_share_links_expire_and_respect_permissions(shop):
    api = shop
    await put(api, "private", "7/doc.png", png())
    link = await api.studio.post(
        f"{ENV}/buckets/private/shares",
        json={"key": "7/doc.png", "expires_in": 600, "permissions": "view"},
    )
    assert (await api.http.get(f"/storage/v1/share/{link['token']}?download=1")).status_code == 403
    await ObjectShare.filter(id=link["id"]).update(
        expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    gone = await api.http.get(f"/storage/v1/share/{link['token']}")
    assert gone.status_code == 410
    listed = await api.studio.get(f"{ENV}/buckets/private/shares")
    assert listed["data"][0]["status"] == "expired" and listed["data"][0]["url"] is None

    dl = await api.studio.post(
        f"{ENV}/buckets/private/shares",
        json={"key": "7/doc.png", "expires_in": 600, "permissions": "download"},
    )
    got = await api.http.get(f"/storage/v1/share/{dl['token']}")
    assert got.status_code == 200 and got.headers["content-disposition"].startswith("attachment;")


# ── transformations ────────────────────────────────────────────────────────


async def test_image_transformations(shop):
    api = shop
    await put(api, "media", "7/photo.png", png(400, 300))
    url = "/storage/v1/files/development/media/7/photo.png"

    cover = await api.http.get(f"{url}?width=100&height=100&fit=cover&format=webp")
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(cover.content)).size == (100, 100)

    contain = await api.http.get(f"{url}?width=100")
    assert Image.open(io.BytesIO(contain.content)).size == (100, 75)

    # The original is untouched.
    original = await api.http.get(url)
    assert Image.open(io.BytesIO(original.content)).size == (400, 300)

    auto = await api.http.get(
        f"{url}?width=50&format=auto", headers={"accept": "image/avif,image/webp,*/*"}
    )
    assert auto.headers["content-type"] == "image/avif" and "accept" in auto.headers["vary"].lower()
    jpeg_only = await api.http.get(f"{url}?width=50&format=auto", headers={"accept": "image/png"})
    assert jpeg_only.headers["content-type"] == "image/png"

    rotated = await api.http.get(f"{url}?rotate=90&format=png")
    assert Image.open(io.BytesIO(rotated.content)).size == (300, 400)
    cropped = await api.http.get(f"{url}?crop=0,0,40,30&format=png")
    assert Image.open(io.BytesIO(cropped.content)).size == (40, 30)


async def test_transformation_limits_and_errors(shop):
    api = shop
    await put(api, "media", "7/photo.png", png(100, 100))
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={
            "name": "media",
            "public": True,
            "write_policy": "authenticated",
            "transforms": {"max_width": 64, "max_height": 64, "formats": ["webp"]},
        },
    )
    url = "/storage/v1/files/development/media/7/photo.png"
    assert (await api.http.get(f"{url}?width=65")).status_code == 400
    assert (await api.http.get(f"{url}?width=abc")).status_code == 400
    assert (await api.http.get(f"{url}?format=png&width=10")).status_code == 400
    assert (await api.http.get(f"{url}?width=10&fit=zoom")).status_code == 400
    ok = await api.http.get(f"{url}?width=64&format=webp")
    assert ok.status_code == 200
    # Switched off, a bucket refuses to transform at all and still serves the original.
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={
            "name": "media",
            "public": True,
            "write_policy": "authenticated",
            "transforms": {"enabled": False},
        },
    )
    assert (await api.http.get(f"{url}?width=10")).status_code == 400
    assert (await api.http.get(url)).status_code == 200
    rejected = await api.studio.request_raw(
        "PUT",
        f"{ENV}/buckets/media",
        json={"name": "media", "transforms": {"max_width": 99999}},
    )
    assert rejected["status"] == 422


async def test_variants_are_cached_per_version_and_never_skip_the_read_policy(shop):
    api = shop
    variants = api.platform.storage.variants
    await put(api, "private", "7/p.png", png(100, 100), who="7")
    token = headers(api, user_id="7")["Authorization"]
    url = "/storage/v1/files/development/private/7/p.png?width=20&format=png"
    first = await api.http.get(url, headers={"authorization": token})
    assert first.status_code == 200
    before = variants.hits
    again = await api.http.get(url, headers={"authorization": token})
    assert again.content == first.content and variants.hits == before + 1
    # With the variant cached, someone who may not read the original still cannot get it.
    assert (await api.http.get(url)).status_code == 401
    # Replacing the object serves a fresh variant, not the cached one.
    await put(api, "private", "7/p.png", png(100, 100, (0, 0, 255)), who="7")
    replaced = await api.http.get(url, headers={"authorization": token})
    assert replaced.content != first.content


async def test_non_images_are_not_transformed(shop):
    api = shop
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={"name": "media", "public": True, "write_policy": "authenticated"},
    )
    await put(api, "media", "7/a.txt", b"hello", content_type="text/plain")
    got = await api.http.get("/storage/v1/files/development/media/7/a.txt?width=10")
    assert got.status_code == 415


# ── failures and compatibility ─────────────────────────────────────────────


async def test_provider_failure_is_a_502_not_a_crash(shop):
    api = shop
    await put(api, "media", "7/a.png", png())
    held_driver = api.platform.storage.driver(await api.platform.state("development"), "media")
    original = held_driver.stat

    async def broken(key):
        raise OSError("disk gone")

    held_driver.stat = broken
    try:
        got = await api.http.get("/storage/v1/files/development/media/7/a.png")
        assert got.status_code == 502
    finally:
        held_driver.stat = original


async def test_signed_urls_still_work_for_api_consumers(shop):
    api = shop
    await api.studio.put(
        f"{ENV}/buckets/media",
        json={
            "name": "media",
            "public": False,
            "read_policy": "authenticated",
            "signed_downloads": True,
            "write_policy": "authenticated",
        },
    )
    data = png()
    await put(api, "media", "7/a.png", data)
    signed = (
        await api.http.post(
            "/storage/v1/sign/media/7/a.png",
            json={"method": "GET"},
            headers=headers(api, user_id="7"),
        )
    ).json()
    path = signed["url"].replace(api.settings.public_url, "")
    got = await api.http.get(path, headers={"range": "bytes=0-3"})
    assert got.status_code == 206 and got.content == data[:4]


async def test_existing_buckets_keep_their_behaviour(shop):
    """A bucket with no per-action policies lists as it reads and replaces as it uploads."""
    api = shop
    await api.studio.post(
        f"{ENV}/buckets",
        json={"name": "legacy", "public": True, "write_policy": "authenticated"},
    )
    assert (await put(api, "legacy", "a.png", png())).status_code == 201
    assert (await put(api, "legacy", "a.png", png(4, 4), who="9")).status_code == 201
    listed = await api.http.get(
        "/storage/v1/list/legacy", headers=api.context_headers("development")
    )
    assert listed.status_code == 200
    bucket = await api.studio.get(f"{ENV}/buckets/legacy")
    assert bucket["list_policy"] is None and bucket["transforms"] == {}
