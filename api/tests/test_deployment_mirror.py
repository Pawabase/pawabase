"""A function bundle survives losing the volume it was unpacked onto."""

import base64
import hashlib
import io
import shutil
import tarfile

from sillo.storage import MemoryDriver

ENV = "/platform/v1/envs/development"


def make_bundle(answer: str = "first") -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        source = (
            "from pawabase_core.functions import function\n"
            f"@function('mirrored', policy='public')\nasync def mirrored(ctx): return {{'answer': {answer!r}}}\n"
        ).encode()
        info = tarfile.TarInfo("functions/mirrored.py")
        info.size = len(source)
        archive.addfile(info, io.BytesIO(source))
    return raw.getvalue()


async def deploy(api, data: bytes) -> dict:
    created = await api.studio.post(
        f"{ENV}/function-deployments", json={"archive": base64.b64encode(data).decode()}
    )
    return created["deployment"]


def wipe_volume(api) -> None:
    shutil.rmtree(api.platform.deployments.root, ignore_errors=True)
    api.platform.deployments._seen.clear()


async def test_nothing_is_mirrored_with_local_storage(api):
    assert api.platform.deployment_mirror.enabled is False
    await deploy(api, make_bundle())
    assert await api.platform.deployment_mirror.restore() == []


async def test_a_deploy_is_kept_in_storage_and_restored_after_the_volume_is_lost(api):
    mirror = api.platform.deployment_mirror
    mirror._driver = MemoryDriver()
    data = make_bundle()
    deployment = await deploy(api, data)
    assert await mirror.get("development", "main", deployment["id"]) == data

    wipe_volume(api)
    assert api.platform.deployments.stamp("development", "main") is None
    assert await mirror.restore() == [deployment["id"]]
    assert api.platform.deployments.stamp("development", "main")["id"] == deployment["id"]
    invoked = await api.studio.post(f"{ENV}/functions/mirrored/invoke", json={"input": {}})
    assert invoked["result"] == {"answer": "first"}


async def test_restoring_leaves_a_deployment_that_is_already_there_alone(api):
    api.platform.deployment_mirror._driver = MemoryDriver()
    await deploy(api, make_bundle())
    assert await api.platform.deployment_mirror.restore() == []


async def test_only_the_active_bundle_comes_back(api):
    api.platform.deployment_mirror._driver = MemoryDriver()
    await deploy(api, make_bundle("first"))
    second = await deploy(api, make_bundle("second"))
    wipe_volume(api)
    assert await api.platform.deployment_mirror.restore() == [second["id"]]
    invoked = await api.studio.post(f"{ENV}/functions/mirrored/invoke", json={"input": {}})
    assert invoked["result"] == {"answer": "second"}


async def test_a_mirrored_copy_that_does_not_match_its_checksum_is_refused(api, caplog):
    mirror = api.platform.deployment_mirror
    mirror._driver = MemoryDriver()
    deployment = await deploy(api, make_bundle())
    key = mirror.key("development", "main", deployment["id"])

    async def tampered():
        yield make_bundle("tampered")

    await mirror._driver.write(key, tampered())
    wipe_volume(api)
    assert hashlib.sha256(make_bundle("tampered")).hexdigest() != deployment["checksum"]
    assert await mirror.restore() == []
    assert "does not match its checksum" in caplog.text


async def test_a_missing_mirrored_copy_is_reported_not_fatal(api, caplog):
    api.platform.deployment_mirror._driver = MemoryDriver()
    await deploy(api, make_bundle())
    api.platform.deployment_mirror._driver = MemoryDriver()  # an empty store
    wipe_volume(api)
    assert await api.platform.deployment_mirror.restore() == []
    assert "no mirrored copy exists" in caplog.text


async def test_a_rollback_works_from_the_mirror_when_the_local_archive_is_gone(api):
    api.platform.deployment_mirror._driver = MemoryDriver()
    first = await deploy(api, make_bundle("first"))
    await deploy(api, make_bundle("second"))
    wipe_volume(api)
    rolled = await api.studio.post(f"{ENV}/function-deployments/{first['id']}/activate")
    assert rolled["deployment"]["id"] == first["id"]
    invoked = await api.studio.post(f"{ENV}/functions/mirrored/invoke", json={"input": {}})
    assert invoked["result"] == {"answer": "first"}


async def test_a_failing_store_does_not_fail_the_deploy(api, caplog):
    class Broken(MemoryDriver):
        async def write(self, *args, **kwargs):
            raise OSError("store is down")

    api.platform.deployment_mirror._driver = Broken()
    deployment = await deploy(api, make_bundle())
    assert deployment["status"] == "active"
    assert "was not mirrored to storage" in caplog.text


async def test_s3_storage_turns_the_mirror_on_under_a_private_prefix(api):
    from app.storage.s3 import S3Driver

    settings = api.platform.settings
    settings.storage_endpoint = "http://minio:9000"
    settings.storage_bucket = "acme"
    settings.storage_prefix = "tenants/acme"
    mirror = api.platform.deployment_mirror
    assert mirror.enabled is True
    driver = mirror.driver()
    assert isinstance(driver, S3Driver) and driver.bucket == "acme"
    assert driver.prefix == "tenants/acme/_pawabase/deployments/"
    settings.deployment_mirror = False
    mirror._driver = None
    assert mirror.enabled is False
    await driver.close()
