import os
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")


@pytest.fixture
def settings(tmp_path):
    from app.config import ApiSettings

    return ApiSettings(
        _env_file=None,
        app_env="testing",
        database_url=f"sqlite://{tmp_path}/api.db",
        db_generate_schemas=True,
        default_data_url=f"sqlite://{tmp_path}/data/{{env}}.db",
        storage_root=str(tmp_path / "objects"),
        code_path=str(tmp_path / "code"),
        public_url="http://gateway.test",
        inline_worker=True,
        record_buffer_seconds=0,  # records are written at once, so a test can read them right after the call
    )


@dataclass
class Api:
    app: Any
    http: Any
    studio: Any
    settings: Any
    platform: Any

    def context_headers(self, env: str, role: str = "anon", scopes=()) -> dict[str, str]:
        from pawabase_core.context import CONTEXT_HEADER, PlatformContext
        from pawabase_core.tokens import issue_context_token

        context = PlatformContext(env=env, role=role, key_id="test", scopes=tuple(scopes))
        return {CONTEXT_HEADER: issue_context_token(self.settings.internal_secret, context)}

    def user_headers(
        self, env: str, user_id: str = "1", roles=(), perms=(), email=None
    ) -> dict[str, str]:
        from pawabase_core.tokens import issue_user_token

        token = issue_user_token(
            self.settings.jwt_master_secret,
            env=env,
            user_id=user_id,
            jti=f"j{user_id}",
            session_id="s",
            roles=list(roles),
            permissions=list(perms),
            email=email,
        )
        return {**self.context_headers(env), "Authorization": f"Bearer {token}"}

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait until the inline worker has emptied every queue."""
        import asyncio

        from app.platform import PLATFORM_QUEUES
        from database.models import JobRun

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            sizes = [await self.platform.queue.size(q) for q in PLATFORM_QUEUES]
            busy = await JobRun.filter(status__in=["queued", "active", "retrying"]).count()
            if not any(sizes) and not busy:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("queues did not drain")


@pytest.fixture
async def api(settings):
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from pawabase_core.clients import ServiceClient
    from pawabase_core.functions import clear_functions

    # The function registry is process-wide and a runtime has one owner in it, so
    # what one test loaded would otherwise still be there for the next.
    clear_functions()
    app = create_app(settings)
    # The lifespan is driven directly rather than through the client's context
    # manager: pytest-asyncio runs fixture setup and teardown in different
    # tasks, which anyio's cancel scopes refuse.
    await app._startup()
    http = AsyncTestClient(app, base_url="http://api.test")
    studio = ServiceClient(
        "http://api.test", secret=settings.internal_secret, issuer="studio", audience="api", app=app
    )
    try:
        yield Api(
            app=app, http=http, studio=studio, settings=settings, platform=app.state["platform"]
        )
    finally:
        await studio.close()
        await http.aclose()
        await app._shutdown()
        clear_functions()
