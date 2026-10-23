"""What Python you mount next to the server (``routes.py``) uses to act like the platform's own routes.

A mounted router is ordinary Sillo code. This module gives its handlers the two things the compiled routes have: the policy check, and the
platform's runtime (the same data, events, secrets, mail and HTTP calls a flow uses), acting for the caller::

    from sillo import HttpContext, Router
    from pawabase_server.extend import authorize

    router = Router(prefix="/x/billing")

    @router.post("/invoices")
    async def create(ctx: HttpContext):
        call = await authorize(ctx, "authenticated")
        return await call.runtime.resource_create("invoices", {"owner_id": call.auth["user_id"]})

Mounted code is trusted: it runs in the server process with the server's privileges, so it is for the operator's own code, never for a customer's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sillo.auth import useAuth
from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied

from pawabase_server.core.context import PlatformContext, require_context
from pawabase_server.environments.state import EnvironmentState
from pawabase_server.flows.api_runtime import ApiRuntime
from pawabase_server.platform import Platform, get_platform
from pawabase_server.policies import PolicyGate, build_policy_context


@dataclass(slots=True)
class Call:
    """One authorized request: who is calling, the environment they called, and a runtime that acts for them."""

    ctx: Any
    platform: Platform
    context: PlatformContext
    state: EnvironmentState
    #: The caller as policies see it (``user_id``, ``roles``, and the session's own claims such as an organization).
    auth: dict[str, Any]
    #: The API key's role and scopes (``is_service`` is true for a secret key).
    credential: dict[str, Any]
    runtime: ApiRuntime

    @property
    def env(self) -> str:
        return self.state.env_name


async def authorize(ctx: Any, policy: Any = "authenticated", *, input: Any = None) -> Call:
    """Check that the caller satisfies *policy* and return the :class:`Call`.

    *policy* is anything a resource's operation accepts: ``"public"``, ``"authenticated"``, ``"service"``, the name of one of the
    environment's policies, ``"role:admin"``, or a condition. Raises the same 401 and 403 the compiled routes do. Pass the request's body
    as *input* when the policy has conditions on ``$input``.
    """
    platform = get_platform()
    context = require_context(ctx)
    state = await platform.state_for(context)
    gate = PolicyGate(policy, engine=state.engine)
    if input is None:
        await gate.authenticate(ctx)
    else:
        # The body is known, so the policy is decided once, with it, after the caller is identified.
        await useAuth.authenticate(gate, ctx)
        decision = await state.engine.check(policy, build_policy_context(ctx, input=input))
        if not decision:
            user = ctx.scope.get("user")
            if user is None or not getattr(user, "is_authenticated", False):
                raise AuthenticationFailed("Authentication required")
            raise PermissionDenied(decision.reason)
    policy_context = build_policy_context(ctx, input=input)
    try:
        request_id = ctx.state.request_id
    except Exception:
        request_id = None
    runtime = ApiRuntime(platform, state, auth=policy_context["auth"], request_id=request_id)
    return Call(
        ctx=ctx,
        platform=platform,
        context=context,
        state=state,
        auth=policy_context["auth"],
        credential=policy_context["credential"],
        runtime=runtime,
    )
