import pytest
from sillo import HttpContext
from sillo.testclient import AsyncTestClient

from pawabase_core.auth import ProjectUserBackend
from pawabase_core.clients import ServiceClient, ServiceError
from pawabase_core.context import CONTEXT_HEADER, PlatformContext, current_context
from pawabase_core.events import EventBus, PlatformEvent
from pawabase_core.policies import PolicyGate
from pawabase_core.service import SERVICE_ONLY, create_service
from pawabase_core.settings import PlatformSettings
from pawabase_core.tokens import (
    TokenInvalid,
    issue_context_token,
    issue_service_token,
    issue_user_token,
    verify_context_token,
    verify_service_token,
)

SETTINGS = PlatformSettings(_env_file=None)
CTX = PlatformContext(project="acme", env="dev", role="anon", key_id="k1")


def user_token(project="acme", env="dev", user_id="7", roles=None):
    return issue_user_token(
        SETTINGS.jwt_master_secret,
        project=project,
        env=env,
        user_id=user_id,
        jti="j",
        session_id="s",
        roles=roles or [],
    )


def build_app():
    app = create_service(
        "test",
        SETTINGS,
        title="Test",
        description="test",
        backends=[ProjectUserBackend(SETTINGS.jwt_master_secret)],
    )

    @app.get("/whoami", auth=PolicyGate("authenticated"))
    async def whoami(ctx: HttpContext):
        context = current_context(ctx)
        return {
            "user": ctx.user.identity,
            "kind": ctx.user.kind,
            "project": context.project if context else None,
        }

    @app.get("/admins", auth=PolicyGate("role:admin"))
    async def admins(ctx: HttpContext):
        return {"ok": True}

    @app.get("/internal-only", auth=SERVICE_ONLY)
    async def internal(ctx: HttpContext):
        return {"caller": ctx.user.claims.get("svc"), "kind": ctx.user.kind}

    @app.get("/health/v1")
    async def application_health(ctx: HttpContext):
        return {"status": "application-ok"}

    return app


def test_token_round_trips_and_isolation():
    token = issue_context_token(SETTINGS.internal_secret, CTX)
    assert verify_context_token(token, SETTINGS.internal_secret) == CTX
    with pytest.raises(TokenInvalid):
        verify_context_token(token, "another-secret-of-sufficient-length")
    service = issue_service_token(SETTINGS.internal_secret, issuer="studio", audience="api")
    assert (
        verify_service_token(service, SETTINGS.internal_secret, audience="api")["svc"] == "studio"
    )
    with pytest.raises(TokenInvalid):
        verify_service_token(service, SETTINGS.internal_secret, audience="akountz")


async def test_context_user_and_policy_gate():
    app = build_app()
    context_header = {CONTEXT_HEADER: issue_context_token(SETTINGS.internal_secret, CTX)}
    async with AsyncTestClient(app, base_url="http://t") as client:
        assert (await client.get("/whoami")).status_code == 401
        ok = await client.get(
            "/whoami", headers={**context_header, "Authorization": f"Bearer {user_token()}"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"user": "7", "kind": "user", "project": "acme"}
        assert ok.headers.get("x-request-id")

        # A token for another environment is not accepted in this one.
        other = await client.get(
            "/whoami",
            headers={**context_header, "Authorization": f"Bearer {user_token(env='prod')}"},
        )
        assert other.status_code == 401

        forbidden = await client.get(
            "/admins", headers={**context_header, "Authorization": f"Bearer {user_token()}"}
        )
        assert forbidden.status_code == 403
        allowed = await client.get(
            "/admins",
            headers={**context_header, "Authorization": f"Bearer {user_token(roles=['admin'])}"},
        )
        assert allowed.status_code == 200

        # A forged context header is ignored.
        forged = {CONTEXT_HEADER: issue_context_token("x" * 40, CTX)}
        assert (
            await client.get(
                "/whoami", headers={**forged, "Authorization": f"Bearer {user_token()}"}
            )
        ).status_code == 401


async def test_service_client_and_telemetry():
    app = build_app()
    client = ServiceClient(
        "http://unused", secret=SETTINGS.internal_secret, issuer="studio", audience="test", app=app
    )
    assert await client.get("/internal-only") == {"caller": "studio", "kind": "service"}
    wrong = ServiceClient(
        "http://unused",
        secret=SETTINGS.internal_secret,
        issuer="studio",
        audience="elsewhere",
        app=app,
    )
    with pytest.raises(ServiceError) as raised:
        await wrong.get("/internal-only")
    assert raised.value.status == 401
    raw = await wrong.request_raw("GET", "/internal-only")
    assert raw["status"] == 401 and raw["body"]

    assert await client.get("/health/v1") == {"status": "application-ok"}
    assert await client.get("/health") == {"status": "ok", "service": "test"}

    telemetry = await client.get("/internal/v1/telemetry/requests")
    paths = [record["path"] for record in telemetry["requests"]]
    assert "/internal-only" in paths
    assert "/health/v1" in paths
    assert "/health" not in paths
    summary = await client.get("/internal/v1/telemetry/summary")
    assert summary["total"] >= 2
    await client.close()
    await wrong.close()


async def test_memory_event_bus():
    bus = EventBus("memory", source="test")
    seen: list[PlatformEvent] = []

    @bus.subscribe
    async def collect(event):
        seen.append(event)

    await bus.start()
    event_id = await bus.emit("order.paid", project="acme", env="dev", payload={"id": 1})
    await bus.stop()
    assert seen[0].id == event_id and seen[0].source == "test" and seen[0].matches("order.*")
