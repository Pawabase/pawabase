"""CRUD for every kind of definition, validated before it is stored.

Each kind gets the same five routes under
``/platform/v1/projects/{ref}/envs/{env}/<kind>``: list, create, get, replace,
delete. What differs is the request model and the validation. A policy that
does not parse, a flow that references a missing block, or a cron expression
that does not compile is refused with a 422 that says why, rather than being
stored and failing later.
"""

from __future__ import annotations

import copy
import re
import secrets as token_source
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app.platform import Platform
from app.releases import DEFINITIONS, snapshot_environment
from database.models import (
    Branch,
    Bucket,
    EventSubscription,
    Flow,
    InboundHook,
    MailTemplate,
    PolicyDef,
    Resource,
    RouteDef,
    Schedule,
    SchemaDef,
    TransformerDef,
    WebhookEndpoint,
)
from pawabase_kit.flows import validate_flow
from pawabase_kit.policies import PolicyEngine, PolicyError, validate_condition
from pawabase_kit.schemas import SchemaError, validate_fields
from pawabase_kit.transformers import TransformerError, validate_transformer
from routes.common import NAME_PATTERN, OPERATOR, audit, changed, dump, get_environment

RESERVED_RESOURCE_NAMES = {"docs", "openapi.json", "x", "rpc", "health", "internal", "platform"}
ROUTE_PATH = re.compile(r"^(/[A-Za-z0-9_.\-]+|/\{[A-Za-z_][A-Za-z0-9_]*(:path)?\})+$")


def _invalid(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


# ── request models ───────────────────────────────────────────────────────────


class SchemaBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
    description: str = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)


class TransformerBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    definition: dict[str, Any]


class PolicyBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    condition: Any


class OperationSettings(BaseModel):
    enabled: bool = False
    policy: Any = None


class ResourceBody(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    description: str = ""
    table: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    primary_key: str = Field(default="id", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    id_type: Literal["integer", "uuid"] = "integer"
    fields: list[dict[str, Any]] = Field(default_factory=list)
    operations: dict[Literal["list", "get", "create", "update", "delete"], OperationSettings] = (
        Field(default_factory=dict)
    )
    relations: list[dict[str, Any]] = Field(default_factory=list)
    transformer: Any = None
    cache_ttl: int = Field(default=0, ge=0, le=86400)
    rate_limit: dict[str, int] = Field(default_factory=dict)
    events: bool = True
    realtime: bool = False
    timestamps: bool = True
    owner_field: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    tags: list[str] = Field(default_factory=list)


class RouteBody(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: str
    name: str = ""
    description: str = ""
    policy: Any = None
    input_fields: list[dict[str, Any]] | None = None
    input_schema: str | None = None
    response_schema: str | None = None
    transformer: Any = None
    handler_type: Literal["flow", "function"] = "flow"
    handler: str = Field(min_length=1)
    rate_limit: dict[str, int] = Field(default_factory=dict)
    cache_ttl: int = Field(default=0, ge=0, le=86400)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class FlowBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    definition: dict[str, Any]
    enabled: bool = True
    timeout: float = Field(default=60.0, gt=0, le=900)
    record_runs: bool = True


class BucketBody(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    description: str = ""
    public: bool = False
    read_policy: Any = None
    write_policy: Any = None
    accepts: list[str] = Field(default_factory=list)
    max_bytes: int = Field(default=0, ge=0)
    signed_uploads: bool = True


class MailTemplateBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    subject: str = ""
    html: str = ""
    text: str = ""


class SubscriptionBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    event: str = Field(min_length=1, max_length=128)
    target_type: Literal["flow", "function", "realtime"]
    target: str = ""
    condition: Any = None
    enabled: bool = True


class WebhookBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    url: str = Field(pattern=r"^https?://")
    events: list[str] = Field(default_factory=lambda: ["*"])
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    max_attempts: int = Field(default=5, ge=1, le=10)
    secret: str | None = Field(default=None, min_length=16)


class InboundHookBody(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = ""
    description: str = ""
    verification: Literal["none", "hmac-sha256", "pawabase", "token"] = "hmac-sha256"
    signature_header: str = "x-signature"
    target_type: Literal["event", "flow"] = "event"
    target: str = Field(min_length=1)
    enabled: bool = True
    secret: str | None = Field(default=None, min_length=16)


class ScheduleBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = ""
    cron: str | None = None
    interval_seconds: int | None = Field(default=None, ge=10)
    target_type: Literal["flow", "function", "event"]
    target: str = Field(min_length=1)
    payload: Any = None
    enabled: bool = True


# ── validation ───────────────────────────────────────────────────────────────


def _check_policy_ref(engine: PolicyEngine, ref: Any, where: str) -> None:
    if ref is None:
        return
    try:
        engine.resolve_ref(ref)
    except PolicyError as exc:
        raise _invalid(f"{where}: {exc}") from exc


async def _engine(platform: Platform, ref: str, env: str) -> PolicyEngine:
    return (await platform.state(ref, env)).engine


async def validate_schema(platform, ref, env, body: SchemaBody) -> dict[str, Any]:
    try:
        validate_fields(body.fields)
    except SchemaError as exc:
        raise _invalid(str(exc)) from exc
    return {"name": body.name, "description": body.description, "fields_": body.fields}


async def validate_transformer_body(platform, ref, env, body: TransformerBody) -> dict[str, Any]:
    try:
        validate_transformer(body.definition)
    except TransformerError as exc:
        raise _invalid(str(exc)) from exc
    return body.model_dump()


async def validate_policy(platform, ref, env, body: PolicyBody) -> dict[str, Any]:
    try:
        validate_condition(body.condition)
    except PolicyError as exc:
        raise _invalid(str(exc)) from exc
    return body.model_dump()


async def validate_resource(platform, ref, env, body: ResourceBody) -> dict[str, Any]:
    if body.name in RESERVED_RESOURCE_NAMES:
        raise _invalid(f"{body.name!r} is reserved")
    try:
        validate_fields(body.fields)
    except SchemaError as exc:
        raise _invalid(str(exc)) from exc
    engine = await _engine(platform, ref, env)
    for operation, settings in body.operations.items():
        _check_policy_ref(engine, settings.policy, f"operations.{operation}.policy")
    if isinstance(body.transformer, dict):
        try:
            validate_transformer(body.transformer)
        except TransformerError as exc:
            raise _invalid(f"transformer: {exc}") from exc
    for index, relation in enumerate(body.relations):
        if not {"name", "resource", "field"} <= set(relation):
            raise _invalid(f"relations[{index}] needs name, resource and field")
        if relation.get("type", "belongs_to") not in ("belongs_to", "has_many"):
            raise _invalid(f"relations[{index}].type is belongs_to or has_many")
    if body.rate_limit and not body.rate_limit.get("limit"):
        raise _invalid("rate_limit needs a limit")
    data = body.model_dump()
    data["fields_"] = data.pop("fields")
    data["table"] = body.table or body.name
    data["operations"] = {op: settings.model_dump() for op, settings in body.operations.items()}
    return data


async def validate_route(platform, ref, env, body: RouteBody) -> dict[str, Any]:
    if not ROUTE_PATH.match(body.path):
        raise _invalid("path must look like /orders/{id}/pay")
    state = await platform.state(ref, env)
    _check_policy_ref(state.engine, body.policy, "policy")
    if body.input_fields:
        try:
            validate_fields(body.input_fields)
        except SchemaError as exc:
            raise _invalid(f"input_fields: {exc}") from exc
    for label, schema in (
        ("input_schema", body.input_schema),
        ("response_schema", body.response_schema),
    ):
        if schema and schema not in state.schemas:
            raise _invalid(f"{label}: no schema {schema!r}")
    from app.compiler.build import shadows_resource

    if shadows_resource(body.path, state.resources):
        raise _invalid(
            "the path would shadow a resource's own routes (/<resource> or /<resource>/{id})"
        )
    return body.model_dump()


async def validate_flow_body(platform, ref, env, body: FlowBody) -> dict[str, Any]:
    problems = validate_flow(body.definition)
    if problems:
        raise HTTPException(
            status_code=422, detail={"message": "the flow cannot run", "problems": problems}
        )
    return body.model_dump()


async def validate_bucket(platform, ref, env, body: BucketBody) -> dict[str, Any]:
    engine = await _engine(platform, ref, env)
    for label in ("read_policy", "write_policy"):
        value = getattr(body, label)
        _check_policy_ref(engine, value, label)
        if value is not None and engine.resolve_ref(value).handler is not None:
            raise _invalid(f"{label}: bucket policies are JSON conditions")
    return body.model_dump()


async def validate_mail_template(platform, ref, env, body: MailTemplateBody) -> dict[str, Any]:
    from jinja2 import TemplateSyntaxError

    from app.mail import _jinja

    for label in ("subject", "html", "text"):
        try:
            _jinja.parse(getattr(body, label))
        except TemplateSyntaxError as exc:
            raise _invalid(f"{label}: {exc}") from exc
    return body.model_dump()


async def validate_subscription(platform, ref, env, body: SubscriptionBody) -> dict[str, Any]:
    if body.condition is not None:
        try:
            validate_condition(body.condition)
        except PolicyError as exc:
            raise _invalid(f"condition: {exc}") from exc
    if body.target_type in ("flow", "function") and not body.target:
        raise _invalid("target names the flow or function")
    return body.model_dump()


async def validate_webhook(platform, ref, env, body: WebhookBody) -> dict[str, Any]:
    data = body.model_dump(exclude={"secret"})
    secret = body.secret or f"whsec_{token_source.token_urlsafe(24)}"
    data["secret_ciphertext"] = platform.box.seal(secret)
    data["_reveal"] = {"secret": secret} if body.secret is None else {}
    return data


async def validate_inbound(platform, ref, env, body: InboundHookBody) -> dict[str, Any]:
    data = body.model_dump(exclude={"secret"})
    data["name"] = body.name or body.slug
    data["_reveal"] = {}
    if body.verification != "none":
        secret = body.secret or token_source.token_urlsafe(24)
        data["secret_ciphertext"] = platform.box.seal(secret)
        if body.secret is None:
            data["_reveal"] = {"secret": secret}
    return data


async def validate_schedule(platform, ref, env, body: ScheduleBody) -> dict[str, Any]:
    if bool(body.cron) == bool(body.interval_seconds):
        raise _invalid("give exactly one of cron or interval_seconds")
    if body.cron:
        from sillo.work.scheduler import CronTrigger

        try:
            CronTrigger(body.cron)
        except Exception as exc:
            raise _invalid(f"cron: {exc}") from exc
    return body.model_dump()


# ── views ────────────────────────────────────────────────────────────────────


def secret_free(item: Any) -> dict[str, Any]:
    return dump(item, exclude=("secret_ciphertext",))


KINDS: list[dict[str, Any]] = [
    {"path": "schemas", "model": SchemaDef, "body": SchemaBody, "validate": validate_schema},
    {
        "path": "transformers",
        "model": TransformerDef,
        "body": TransformerBody,
        "validate": validate_transformer_body,
    },
    {"path": "policies", "model": PolicyDef, "body": PolicyBody, "validate": validate_policy},
    {"path": "resources", "model": Resource, "body": ResourceBody, "validate": validate_resource},
    {
        "path": "routes",
        "model": RouteDef,
        "body": RouteBody,
        "validate": validate_route,
        "key": "id",
    },
    {"path": "flows", "model": Flow, "body": FlowBody, "validate": validate_flow_body},
    {"path": "buckets", "model": Bucket, "body": BucketBody, "validate": validate_bucket},
    {
        "path": "mail-templates",
        "model": MailTemplate,
        "body": MailTemplateBody,
        "validate": validate_mail_template,
    },
    {
        "path": "subscriptions",
        "model": EventSubscription,
        "body": SubscriptionBody,
        "validate": validate_subscription,
    },
    {
        "path": "webhooks",
        "model": WebhookEndpoint,
        "body": WebhookBody,
        "validate": validate_webhook,
        "view": secret_free,
    },
    {
        "path": "inbound-hooks",
        "model": InboundHook,
        "body": InboundHookBody,
        "validate": validate_inbound,
        "view": secret_free,
        "key": "slug",
    },
    {"path": "schedules", "model": Schedule, "body": ScheduleBody, "validate": validate_schedule},
]


def _register(r: Router, platform: Platform, kind: dict[str, Any]) -> None:
    path = kind["path"]
    model = kind["model"]
    body_model = kind["body"]
    key_field = kind.get("key", "name")
    view: Callable[[Any], dict[str, Any]] = kind.get("view", dump)
    validate: Callable[..., Awaitable[dict[str, Any]]] = kind["validate"]
    tag = path.replace("-", " ")
    base = f"/projects/{{ref}}/envs/{{env}}/{path}"

    snapshot_key = path.replace("-", "_")
    columns = DEFINITIONS[snapshot_key][1]

    async def working_branch(ctx: HttpContext, environment):
        """Return the checked-out branch, or ``None`` for live ``main``."""
        name = ctx.query_params.get("branch", "main")
        if name == "main":
            return None
        branch = await Branch.get_or_none(environment=environment, name=name)
        if branch is None:
            raise HTTPException(status_code=404, detail=f"branch {name!r} does not exist")
        if not branch.draft:
            branch.draft = await snapshot_environment(environment)
            await branch.save(update_fields=["draft"])
        return branch

    def draft_rows(branch: Branch) -> list[dict[str, Any]]:
        definitions = branch.draft.setdefault("definitions", {})
        return definitions.setdefault(snapshot_key, [])

    def draft_view(environment, row: dict[str, Any]) -> dict[str, Any]:
        # Constructing an unsaved model lets each definition keep its existing
        # public view shape while all data remains inside the branch snapshot.
        return view(model(environment=environment, **copy.deepcopy(row)))

    def row_matches(row: dict[str, Any], key: str) -> bool:
        if key_field == "id":
            return str(row.get("id")) == key
        if key_field == "slug":
            return str(row.get("slug")) == key
        return str(row.get("name")) == key

    async def record_branch_change(ctx: HttpContext, branch: Branch, action: str, target: str) -> None:
        branch.changes = [
            *list(branch.changes or []),
            {"action": action, "target": target, "at": datetime.now(UTC).isoformat()},
        ]
        await branch.save(update_fields=["draft", "changes"])

    async def lookup(environment, key: str):
        value: Any = key
        if key_field == "id":
            if not key.isdigit():
                raise HTTPException(status_code=404, detail="not found")
            value = int(key)
        item = await model.get_or_none(environment=environment, **{key_field: value})
        if item is None:
            raise HTTPException(status_code=404, detail=f"no {path[:-1]} {key!r}")
        return item

    async def list_items(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        branch = await working_branch(ctx, environment)
        if branch is not None:
            return {"data": [draft_view(environment, row) for row in draft_rows(branch)]}
        return {"data": [view(item) for item in await model.filter(environment=environment)]}

    async def create_item(ctx: HttpContext, ref: str, env: str, body):
        environment = await get_environment(ref, env)
        branch = await working_branch(ctx, environment)
        data = await validate(platform, ref, env, body)
        reveal = data.pop("_reveal", {})
        natural = (
            {"slug": data["slug"]}
            if key_field == "slug"
            else (
                {"method": data["method"], "path": data["path"]}
                if model is RouteDef
                else {"name": data["name"]}
            )
        )
        if branch is not None:
            exists = any(
                (row.get("slug") == natural.get("slug")) if key_field == "slug"
                else ((row.get("method") == natural.get("method") and row.get("path") == natural.get("path")) if model is RouteDef else row.get("name") == natural.get("name"))
                for row in draft_rows(branch)
            )
            if exists:
                raise HTTPException(status_code=409, detail=f"{path[:-1]} already exists in this branch")
            row = {column: copy.deepcopy(data.get(column)) for column in columns if column in data}
            # Routes are normally addressed by a database id. New branch-only
            # routes do not have one until merge, so give them a stable
            # negative draft id that can still be edited or deleted in Studio.
            if key_field == "id":
                ids = [int(item["id"]) for item in draft_rows(branch) if item.get("id") is not None]
                row["id"] = min([0, *ids]) - 1
            else:
                row.setdefault("id", None)
            draft_rows(branch).append(row)
            await record_branch_change(ctx, branch, f"{path}.created", str(getattr(body, key_field, data.get("name", ""))))
            await audit(ctx, f"branch.{path}.created", project=ref, env=env, target=str(getattr(body, key_field, data.get("name", ""))), details={"branch": branch.name})
            return created({**draft_view(environment, row), **reveal})
        if await model.filter(environment=environment, **natural).exists():
            raise HTTPException(status_code=409, detail=f"{path[:-1]} already exists")
        item = await model.create(environment=environment, **data)
        await changed(ctx, environment, f"{path}.created", str(getattr(item, key_field)))
        return created({**view(item), **reveal})

    async def get_item(ctx: HttpContext, ref: str, env: str, key: str):
        environment = await get_environment(ref, env)
        branch = await working_branch(ctx, environment)
        if branch is not None:
            row = next((item for item in draft_rows(branch) if row_matches(item, key)), None)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no {path[:-1]} {key!r} in branch {branch.name!r}")
            return draft_view(environment, row)
        return view(await lookup(environment, key))

    async def replace_item(ctx: HttpContext, ref: str, env: str, key: str, body):
        environment = await get_environment(ref, env)
        branch = await working_branch(ctx, environment)
        if branch is not None:
            row = next((item for item in draft_rows(branch) if row_matches(item, key)), None)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no {path[:-1]} {key!r} in branch {branch.name!r}")
            data = await validate(platform, ref, env, body)
            reveal = data.pop("_reveal", {})
            if model in (WebhookEndpoint, InboundHook) and getattr(body, "secret", None) is None:
                data.pop("secret_ciphertext", None)
                reveal = {}
            row.update(copy.deepcopy(data))
            await record_branch_change(ctx, branch, f"{path}.updated", key)
            await audit(ctx, f"branch.{path}.updated", project=ref, env=env, target=key, details={"branch": branch.name})
            return {**draft_view(environment, row), **reveal}
        item = await lookup(environment, key)
        data = await validate(platform, ref, env, body)
        reveal = data.pop("_reveal", {})
        if model in (WebhookEndpoint, InboundHook) and getattr(body, "secret", None) is None:
            # Keep the existing secret unless a new one was given.
            data.pop("secret_ciphertext", None)
            reveal = {}
        for field, value in data.items():
            setattr(item, field, value)
        await item.save()
        await changed(ctx, environment, f"{path}.updated", key)
        return {**view(item), **reveal}

    async def delete_item(ctx: HttpContext, ref: str, env: str, key: str):
        environment = await get_environment(ref, env)
        branch = await working_branch(ctx, environment)
        if branch is not None:
            rows = draft_rows(branch)
            index = next((i for i, item in enumerate(rows) if row_matches(item, key)), None)
            if index is None:
                raise HTTPException(status_code=404, detail=f"no {path[:-1]} {key!r} in branch {branch.name!r}")
            rows.pop(index)
            await record_branch_change(ctx, branch, f"{path}.deleted", key)
            await audit(ctx, f"branch.{path}.deleted", project=ref, env=env, target=key, details={"branch": branch.name})
            return no_content()
        item = await lookup(environment, key)
        await item.delete()
        await changed(ctx, environment, f"{path}.deleted", key)
        return no_content()

    r.get(
        base,
        handler=list_items,
        auth=OPERATOR,
        tags=[tag],
        name=f"{path}.list",
        summary=f"List {tag}",
    )
    r.post(
        base,
        handler=create_item,
        auth=OPERATOR,
        tags=[tag],
        name=f"{path}.create",
        request_model=body_model,
        summary=f"Create {tag[:-1]}",
    )
    r.get(
        f"{base}/{{key}}",
        handler=get_item,
        auth=OPERATOR,
        tags=[tag],
        name=f"{path}.get",
        summary=f"Get {tag[:-1]}",
    )
    r.put(
        f"{base}/{{key}}",
        handler=replace_item,
        auth=OPERATOR,
        tags=[tag],
        name=f"{path}.replace",
        request_model=body_model,
        summary=f"Replace {tag[:-1]}",
    )
    r.delete(
        f"{base}/{{key}}",
        handler=delete_item,
        auth=OPERATOR,
        tags=[tag],
        name=f"{path}.delete",
        summary=f"Delete {tag[:-1]}",
    )


def register(r: Router, platform: Platform) -> None:
    for kind in KINDS:
        _register(r, platform, kind)
