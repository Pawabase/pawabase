"""The rules tester: ask "may this caller do this?" and see why.

A rule is only as good as what it was last tested against. This endpoint evaluates an environment's real policies with the
same engine, the same context shape and the same list-planning the data API uses, for any caller (a real user, a made-up
one, anonymous, or a service) and any record, and answers with the decision, the status the API would return, and the
comparison-by-comparison trace behind it. For a list it also runs the plan against the table, so "what would this user see?"
is a count and a sample, not a guess.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException

from pawabase_server.core.ids import is_ulid
from pawabase_server.core.principal import ANONYMOUS_POLICY_CONTEXT, Principal
from pawabase_server.data.store import Filter
from pawabase_server.db.models import AuthUser, Membership, Organization
from pawabase_server.identity import rbac
from pawabase_server.platform import Platform
from pawabase_server.policies import Policy, PolicyError
from pawabase_server.policies.engine import mentions, trace
from pawabase_server.routes.common import MANAGE, get_environment

OPERATIONS = ("list", "get", "create", "update", "delete")
METHODS = {"list": "GET", "get": "GET", "create": "POST", "update": "PATCH", "delete": "DELETE"}
#: What the data API treats an operation without its own policy as.
DEFAULT_POLICY = "authenticated"


class CallerBody(BaseModel):
    kind: Literal["anonymous", "user", "service"] = "user"
    #: A real user of this environment (their roles and permissions are loaded), or any id to test as.
    user_id: str | None = None
    #: Fill in to override what is loaded, or to test a user who does not exist.
    email: str | None = None
    roles: list[str] | None = None
    permissions: list[str] | None = None
    org: str | None = None
    org_role: str | None = None
    aal: str = "aal1"


class RulesTestBody(BaseModel):
    #: A resource and one of its operations: tests the policy that operation is configured with.
    resource: str | None = None
    operation: Literal["list", "get", "create", "update", "delete"] = "list"
    #: Without a resource: any policy reference (a name, ``role:admin``, or an inline condition).
    policy: Any = None
    caller: CallerBody = Field(default_factory=CallerBody)
    #: The record being read, changed or deleted; or its id, to load the real one.
    record: dict[str, Any] | None = None
    record_id: str | None = None
    #: The submitted data of a create or update.
    input: dict[str, Any] | None = None
    #: How many rows a list preview reads from the table.
    sample: int = Field(default=200, ge=1, le=1000)


async def _principal(env: str, body: CallerBody) -> tuple[Principal | None, str, list[str]]:
    """The caller as a principal, where its values came from (``user``, ``manual``…), and anything worth warning about."""
    if body.kind == "anonymous":
        return None, "anonymous", []
    if body.kind == "service":
        return (
            Principal("service", {"svc": "studio", "roles": ["admin"], "perms": ["*"]}),
            "service",
            [],
        )
    warnings: list[str] = []
    user = None
    if body.user_id and is_ulid(body.user_id):
        user = await AuthUser.get_or_none(env=env, id=body.user_id, deleted_at=None)
    if body.user_id and user is None:
        warnings.append(f"No user has the id {body.user_id}; testing with the values you entered.")
    claims: dict[str, Any] = {
        "sub": body.user_id or "tester",
        "email": body.email,
        "roles": body.roles or [],
        "perms": body.permissions or [],
        "org": body.org,
        "org_role": body.org_role,
        "aal": body.aal,
    }
    source = "manual"
    if user is not None:
        source = "user"
        claims.update(
            sub=str(user.id),
            email=body.email or user.email,
            roles=body.roles if body.roles is not None else await rbac.roles_of(user),
            perms=body.permissions
            if body.permissions is not None
            else await rbac.permissions_of(user),
        )
        if user.app_metadata:
            claims["app"] = user.app_metadata
        if user.user_metadata:
            claims["meta"] = user.user_metadata
        if body.org and not body.org_role:
            org = await Organization.get_or_none(env=env, slug=body.org)
            member = await Membership.get_or_none(organization=org, user=user) if org else None
            if member is None:
                warnings.append(
                    f"{user.email} is not a member of {body.org}; org and org_role are empty."
                )
                claims["org"] = None
            else:
                claims["org_role"] = member.role
    return Principal("user", claims), source, warnings


def _status(allowed: bool, authenticated: bool, hidden: bool = False) -> int:
    if allowed:
        return 200
    if hidden:
        return 404
    return 403 if authenticated else 401


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.post(
        f"{base}/policies/test",
        auth=MANAGE,
        tags=["policies"],
        request_model=RulesTestBody,
        summary="Decide a request for a caller and show the reasoning, without performing it",
    )
    async def test_rules(ctx: HttpContext, env: str, body: RulesTestBody):
        await get_environment(env)
        state = await platform.state(env)
        engine = state.engine
        warnings: list[str] = []

        # ── what is being tested ──
        resource = None
        reference = body.policy
        if body.resource:
            resource = state.resources.get(body.resource)
            if resource is None:
                raise HTTPException(status_code=404, detail=f"no resource {body.resource!r}")
            settings = (resource.operations or {}).get(body.operation)
            if not settings or not settings.get("enabled"):
                return {
                    "resource": body.resource,
                    "operation": body.operation,
                    "enabled": False,
                    "allowed": False,
                    "status": 404,
                    "reason": f"{body.operation} is not enabled for {body.resource}, so the API has no such route.",
                    "warnings": [],
                }
            reference = settings.get("policy") or DEFAULT_POLICY
        elif reference is None:
            raise HTTPException(
                status_code=400, detail="give a resource and operation, or a policy"
            )
        try:
            policy = engine.resolve_ref(reference)
        except PolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # ── who is asking ──
        principal, source, extra = await _principal(env, body.caller)
        warnings += extra
        auth = principal.as_policy_context() if principal else dict(ANONYMOUS_POLICY_CONTEXT)
        is_service = body.caller.kind == "service"

        # ── the record ──
        record = body.record
        store = await state.store(resource.name) if resource else None
        if record is None and body.record_id and store is not None:
            try:
                record_id = (
                    int(body.record_id) if store.spec.id_type == "integer" else body.record_id
                )
                record = await store.get(record_id)
            except (ValueError, TypeError):
                record = None
            if record is None:
                raise HTTPException(
                    status_code=404, detail=f"no record {body.record_id!r} in {body.resource}"
                )
        operation = body.operation if resource else "check"
        context: dict[str, Any] = {
            "auth": auth,
            "credential": {
                "role": "service" if is_service else "anon",
                "is_service": is_service,
                "scopes": [],
                "key_id": None,
            },
            "request": {
                "method": METHODS.get(operation, "GET"),
                "path": f"/rest/v1/{resource.name}" if resource else "",
                "ip": None,
                "params": {},
                "query": {},
            },
            "env": env,
            "record": record
            if operation in ("get", "update", "delete", "create", "check")
            else None,
            "input": body.input,
        }
        if operation == "create" and record is None:
            context["record"] = body.input
        condition = policy.condition if policy.handler is None else None
        if condition is not None and not is_service:
            if mentions(condition, "record") and context["record"] is None and operation != "list":
                warnings.append(
                    "This rule reads the record, but no record was given: its fields count as empty."
                )
            if mentions(condition, "input") and body.input is None:
                warnings.append("This rule reads the submitted data, but none was given.")

        # ── the decision ──
        if is_service:
            tree = {
                "op": "service",
                "result": True,
                "note": "Service credentials (secret keys and Studio) bypass policies.",
            }
        elif condition is None:
            tree = {
                "op": "python",
                "result": None,
                "note": f"Policy {policy.name!r} is Python code; it cannot be explained step by step.",
            }
        else:
            tree = trace(condition, context)
        out: dict[str, Any] = {
            "resource": body.resource,
            "operation": body.operation if resource else None,
            "enabled": True,
            "policy": policy.name,
            "reference": reference,
            "condition": condition,
            "caller": {"kind": body.caller.kind, "source": source, "auth": auth},
            "trace": tree,
            "warnings": warnings,
        }

        if operation == "list":
            plan = engine.plan(reference, context)
            out["plan"] = {
                "allowed": plan.allowed,
                "filters": plan.filters,
                "residual": None if isinstance(plan.residual, Policy) else plan.residual,
                "python": isinstance(plan.residual, Policy),
            }
            out["allowed"] = plan.allowed
            out["status"] = _status(plan.allowed, bool(auth["authenticated"]))
            out["reason"] = (
                f"Policy {plan.policy!r} refuses every row, so the request is refused before any query."
                if not plan.allowed
                else "The request is allowed; the caller sees the rows the plan below lets through."
            )
            if plan.allowed and store is not None:
                filters = [Filter(column, "eq", value) for column, value in plan.filters.items()]
                rows, total = await store.list(filters=filters, limit=body.sample, count=True)
                visible, hidden = [], []
                for row in rows:
                    row_context = {**context, "record": row}
                    (visible if await engine.row_allowed(plan, row_context) else hidden).append(row)
                example = None
                if hidden and condition is not None:
                    example = {
                        "row": hidden[0],
                        "trace": trace(condition, {**context, "record": hidden[0]}),
                    }
                out["preview"] = {
                    "table_rows_matching_filters": total,
                    "scanned": len(rows),
                    "visible": len(visible),
                    "hidden": len(hidden),
                    "complete": total is not None and total <= len(rows),
                    "sample": visible[:5],
                    "hidden_example": example,
                }
        elif operation == "get":
            plan = engine.plan(reference, context)
            allowed = plan.allowed
            hidden = False
            filtered_out = record is not None and any(
                str(record.get(k)) != str(v) for k, v in plan.filters.items()
            )
            if allowed and (
                filtered_out
                or (
                    record is not None
                    and plan.residual is not None
                    and not await engine.row_allowed(plan, context)
                )
            ):
                allowed, hidden = False, True
            out["allowed"] = allowed
            out["status"] = _status(allowed, bool(auth["authenticated"]), hidden)
            out["reason"] = (
                "Allowed: the caller can read this record."
                if allowed
                else "The record exists but is not visible to this caller; the API answers 404 so its existence is not revealed."
                if hidden
                else f"Policy {plan.policy!r} refused the request."
            )
        else:
            decision = await engine.check(reference, context)
            out["allowed"] = bool(decision)
            out["status"] = _status(bool(decision), bool(auth["authenticated"]))
            out["reason"] = decision.reason
        return out
