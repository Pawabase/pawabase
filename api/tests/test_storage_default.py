"""Default object storage: MinIO (any S3-compatible service) unless an environment says otherwise."""

import httpx
import pytest
from app.config import ApiSettings, default_storage
from app.storage.manager import StorageManager
from app.storage.s3 import S3Driver


def settings(**values) -> ApiSettings:
    return ApiSettings(_env_file=None, app_env="testing", **values)


class FakeState:
    project_ref = "acme"
    env_name = "prod"

    def __init__(self, storage=None):
        self.infra = {"storage": storage} if storage is not None else {}


class FakePlatform:
    def __init__(self, **values):
        self.settings = settings(**values)

    def resolve_value(self, state, value):
        return value


def manager(**values) -> StorageManager:
    return StorageManager(FakePlatform(**values))


def driver_with(handler, **kwargs) -> S3Driver:
    driver = S3Driver(
        bucket="pawabase",
        endpoint="http://minio:9000",
        access_key="key",
        secret_key="secret",
        **kwargs,
    )
    driver._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return driver


# ── the platform default ─────────────────────────────────────────────────


def test_no_endpoint_means_local_disk():
    assert default_storage(settings()) == {"driver": "local"}


def test_an_endpoint_selects_s3():
    config = default_storage(
        settings(storage_endpoint="http://minio:9000", storage_access_key="a", storage_secret_key="b")
    )
    assert config["driver"] == "s3"
    assert config["endpoint"] == "http://minio:9000"
    assert config["bucket"] == "pawabase"
    assert config["path_style"] is True


def test_an_explicit_driver_wins_over_the_endpoint():
    assert default_storage(settings(storage_driver="memory", storage_endpoint="http://x"))["driver"] == "memory"


def test_an_environment_without_storage_gets_the_default():
    storage = manager(storage_endpoint="http://minio:9000", storage_bucket="files")
    config = storage._config(FakeState())
    assert config["driver"] == "s3" and config["bucket"] == "files"
    driver = storage.driver(FakeState(), "avatars")
    assert isinstance(driver, S3Driver)
    assert driver.prefix == "acme/prod/avatars/"


def test_an_environments_own_storage_overrides_the_default():
    storage = manager(storage_endpoint="http://minio:9000")
    own = {"driver": "s3", "endpoint": "https://r2.example.com", "bucket": "mine"}
    driver = storage.driver(FakeState(own), "avatars")
    assert driver.endpoint == "https://r2.example.com" and driver.bucket == "mine"


def test_an_endpoint_without_a_driver_means_s3():
    storage = manager()
    config = storage._config(FakeState({"endpoint": "https://s3.example.com", "bucket": "b"}))
    assert config["driver"] == "s3"


def test_a_storage_block_without_an_endpoint_stays_local():
    config = manager(storage_endpoint="http://minio:9000")._config(FakeState({"root": "/srv/files"}))
    assert config["driver"] == "local"


# ── the S3 driver ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_bucket_leaves_an_existing_bucket_alone():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200)

    assert await driver_with(handler).ensure_bucket() is False
    assert calls == ["HEAD"]


@pytest.mark.asyncio
async def test_ensure_bucket_creates_a_missing_bucket():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(404 if request.method == "HEAD" else 200)

    assert await driver_with(handler).ensure_bucket() is True
    assert calls == [("HEAD", "/pawabase"), ("PUT", "/pawabase")]


@pytest.mark.asyncio
async def test_ensure_bucket_reports_bad_credentials():
    from sillo.storage.errors import StorageError

    with pytest.raises(StorageError, match="access key"):
        await driver_with(lambda request: httpx.Response(403)).ensure_bucket()


@pytest.mark.asyncio
async def test_a_first_write_creates_the_bucket_and_retries():
    state = {"bucket": False}
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "PUT" and request.url.path == "/pawabase":
            state["bucket"] = True
            return httpx.Response(200)
        if request.method == "HEAD":
            return httpx.Response(200 if state["bucket"] else 404)
        if not state["bucket"]:
            return httpx.Response(404, text="<Error><Code>NoSuchBucket</Code></Error>")
        return httpx.Response(200, headers={"etag": '"abc"'})

    async def body():
        yield b"hello"

    stored = await driver_with(handler).write("a.txt", body(), content_type="text/plain")
    assert stored.size == 5 and stored.etag == "abc"
    assert calls == ["PUT", "HEAD", "PUT", "PUT"]


def test_presigned_urls_use_the_public_endpoint():
    driver = driver_with(lambda request: httpx.Response(200), public_endpoint="http://localhost:9000")
    url = driver.signed_url("a/b.png")
    assert url.startswith("http://localhost:9000/pawabase/a/b.png?")
    assert "X-Amz-Signature=" in url
    internal = driver_with(lambda request: httpx.Response(200)).signed_url("a/b.png")
    assert internal.startswith("http://minio:9000/pawabase/")


@pytest.mark.asyncio
async def test_prepare_default_reports_a_down_service():
    storage = manager(storage_endpoint="http://127.0.0.1:1", storage_bucket="x")
    message = await storage.prepare_default()
    assert "NOT READY" in message
    assert "storage: local (default)" == await manager().prepare_default()


# ── live (opt-in) ────────────────────────────────────────────────────────
# PAWABASE_TEST_S3_ENDPOINT=http://127.0.0.1:9000 PAWABASE_TEST_S3_KEY=... PAWABASE_TEST_S3_SECRET=... pytest


@pytest.mark.asyncio
async def test_against_a_real_s3_compatible_service():
    import os
    import uuid

    endpoint = os.environ.get("PAWABASE_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("set PAWABASE_TEST_S3_ENDPOINT to run against MinIO or another S3 service")
    driver = S3Driver(
        bucket=f"pawabase-test-{uuid.uuid4().hex[:8]}",
        endpoint=endpoint,
        access_key=os.environ.get("PAWABASE_TEST_S3_KEY", ""),
        secret_key=os.environ.get("PAWABASE_TEST_S3_SECRET", ""),
        public_endpoint=endpoint,
    )

    async def body():
        yield b"hello"

    try:
        assert await driver.ensure_bucket() is True
        assert (await driver.write("dir/a.txt", body(), content_type="text/plain")).size == 5
        assert b"".join([chunk async for chunk in driver.read("dir/a.txt")]) == b"hello"
        assert [f.key for f in (await driver.page("dir/")).files] == ["dir/a.txt"]
        async with httpx.AsyncClient() as client:
            assert (await client.get(driver.signed_url("dir/a.txt"))).text == "hello"
        assert await driver.delete("dir/a.txt") is True
    finally:
        await driver.close()
