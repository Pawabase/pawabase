"""With the buffer on, a function call does not wait for its run record."""

import textwrap

import pytest

from database.models import FunctionRun

ENV = "/platform/v1/envs/development"


CODE = textwrap.dedent(
    """
    from pawabase_core.functions import function

    @function("quick.ok", policy="public")
    async def ok(ctx):
        return {"ok": True}
    """
)


@pytest.fixture
def buffered(settings):
    return settings.model_copy(update={"record_buffer_seconds": 60})


async def test_a_function_call_does_not_wait_for_its_run_record(buffered, tmp_path):
    """With the buffer on, the call returns first and the record follows with the flush (and at shutdown)."""
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from pawabase_core.clients import ServiceClient
    from pawabase_core.functions import clear_functions

    code = tmp_path / "code" / "functions"
    code.mkdir(parents=True)
    (code / "quick.py").write_text(CODE)
    clear_functions()
    app = create_app(buffered)
    await app._startup()
    try:
        studio = ServiceClient(
            "http://api.test",
            secret=buffered.internal_secret,
            issuer="studio",
            audience="api",
            app=app,
        )
        await studio.post(
            f"{ENV}/routes",
            json={
                "method": "POST",
                "path": "/quick",
                "handler_type": "function",
                "handler": "quick.ok",
                "policy": "public",
            },
        )
        http = AsyncTestClient(app, base_url="http://api.test")
        from pawabase_core.context import CONTEXT_HEADER, PlatformContext
        from pawabase_core.tokens import issue_context_token

        header = {
            CONTEXT_HEADER: issue_context_token(
                buffered.internal_secret,
                PlatformContext(env="development", role="anon", key_id="t", scopes=()),
            )
        }
        assert (await http.post("/rest/v1/quick", json={}, headers=header)).status_code == 200
        platform = app.state["platform"]
        assert (
            await FunctionRun.filter(function="quick.ok").count() == 0
            and len(platform.records.pending) == 1
        )
        await platform.records.flush()
        assert await FunctionRun.filter(function="quick.ok").count() == 1
        await studio.close()
        await http.aclose()
    finally:
        await app._shutdown()
        clear_functions()
