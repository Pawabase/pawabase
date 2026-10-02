import base64
import io
import tarfile

ENV = "/platform/v1/projects/shop/envs/development"


def bundle() -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        source = b"from pawabase_core.functions import function\n@function('deployed', policy='public')\nasync def deployed(ctx): return {'ok': True}\n"
        info = tarfile.TarInfo("functions/deployed.py")
        info.size = len(source)
        archive.addfile(info, io.BytesIO(source))
    return base64.b64encode(raw.getvalue()).decode()


async def test_deployment_activates_an_artifact_and_records_function_runs(api):
    await api.studio.post("/platform/v1/projects", json={"ref": "shop", "name": "Shop"})
    deployed = await api.studio.post(f"{ENV}/function-deployments", json={"archive": bundle()})
    deployment = deployed["deployment"]
    assert deployment["status"] == "active"
    invoked = await api.studio.post(f"{ENV}/functions/deployed/invoke", json={"input": {}})
    assert invoked["result"] == {"ok": True}
    runs = await api.studio.get(f"{ENV}/function-runs?function=deployed")
    assert runs["data"][0]["deployment_id"] == deployment["id"]
