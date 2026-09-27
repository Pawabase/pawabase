"""Resource operations compiled into Sillo routes.

For a Resource ``posts`` with operations enabled, this registers::

    GET    /posts           list    (filter, sort, paginate, expand, select)
    GET    /posts/{id}      get
    POST   /posts           create  (request_model from the fields)
    PATCH  /posts/{id}      update  (partial request_model)
    DELETE /posts/{id}      delete

Each is an ordinary Sillo route: a Pydantic ``request_model`` compiled from
the field definitions, a policy gate (a ``useAuth`` subclass) on ``auth=``, an
optional rate limit, and OpenAPI metadata. Sillo does the validation, the gating
and the documentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, create_model
from sillo import HttpContext, Query, no_content
from sillo import json as json_response
from sillo.exceptions import HTTPException
from sillo.helpers.strings import pascal_case

from app.compiler.common import PLAN_SCOPE_KEY, PlanGate, cache_key
from app.data.store import MAX_PAGE_SIZE, Filter, ResourceSpec, parse_filters, parse_sort
from app.resources import after_write, resource_tag
from pawabase_kit.policies import build_policy_context
from pawabase_kit.ratelimit import rate_limit_middleware
from pawabase_kit.schemas import compile_model
from pawabase_kit.telemetry import note
from pawabase_kit.transformers import apply_transformer

if TYPE_CHECKING:
    from sillo import SilloApp

    from app.state import EnvironmentState

OPERATIONS = ("list", "get", "create", "update", "delete")
DEFAULT_POLICY = "authenticated"


def operation_settings(resource: Any, operation: str) -> dict[str, Any] | None:
    settings = (resource.operations or {}).get(operation)
    if not settings or not settings.get("enabled"):
        return None
    return settings


def _coerce_id(spec: ResourceSpec, raw: str) -> Any:
    if spec.id_type == "integer":
        if not raw.isdigit():
            raise HTTPException(status_code=404, detail="Not found")
        return int(raw)
    return raw


def _auth_actor(ctx: HttpContext) -> str | None:
    user = ctx.scope.get("user")
    return user.identity if user is not None and getattr(user, "is_authenticated", False) else None


def _is_service(ctx: HttpContext) -> bool:
    from pawabase_kit.policies import credential_context

    return bool(credential_context(ctx).get("is_service"))


def _request_id(ctx: HttpContext) -> str | None:
    try:
        return ctx.state.request_id
    except Exception:
        return ctx.headers.get("x-request-id")


async def _present(state: EnvironmentState, resource: Any, ctx: HttpContext, data: Any) -> Any:
    if resource.transformer:
        context = build_policy_context(ctx)
        return await apply_transformer(
            data, resource.transformer, context=context, registry=state.transformers
        )
    return data


async def _expand(
    state: EnvironmentState, spec: ResourceSpec, rows: list[dict[str, Any]], names: list[str]
) -> None:
    """Attach ``belongs_to`` and ``has_many`` relations requested with ``?expand=``."""
    relations = {relation["name"]: relation for relation in spec.relations}
    for name in names:
        relation = relations.get(name)
        if relation is None:
            raise HTTPException(status_code=400, detail=f"{spec.name} has no relation {name!r}")
        target = await state.store(relation["resource"])
        if relation.get("type", "belongs_to") == "belongs_to":
            ids = sorted(
                {
                    row.get(relation["field"])
                    for row in rows
                    if row.get(relation["field"]) is not None
                },
                key=str,
            )
            related = {
                str(item[target.spec.primary_key]): item
                for item in await target.get_many(target.spec.primary_key, ids)
            }
            for row in rows:
                row[name] = related.get(str(row.get(relation["field"])))
        else:  # has_many: relation["field"] is the foreign key on the target
            ids = [row[spec.primary_key] for row in rows]
            children = await target.get_many(relation["field"], ids)
            for row in rows:
                row[name] = [
                    child
                    for child in children
                    if str(child.get(relation["field"])) == str(row[spec.primary_key])
                ]


def register_resource(app: SilloApp, state: EnvironmentState, resource: Any) -> list[str]:
    """Add the enabled operations of *resource* to *app*. Returns route summaries."""
    spec = state.specs[resource.name]
    platform = state.platform
    title = pascal_case(resource.name)
    tags = [resource.name, *(resource.tags or [])]
    registered: list[str] = []
    registry = state.compiled_schemas
    read_model = compile_model(
        f"{title}",
        spec.fields
        + [
            {
                "name": spec.primary_key,
                "type": "uuid" if spec.id_type == "uuid" else "integer",
                "read_only": True,
            }
        ],
        mode="read",
        registry=registry,
    )
    namespace = f"rl:{state.project_ref}:{state.env_name}:{resource.name}"
    limits = rate_limit_middleware(resource.rate_limit, namespace, platform.settings.redis_url)
    base = f"/{resource.name}"
    item = f"/{resource.name}/{{id}}"

    def gate(operation: str, settings: dict[str, Any]) -> PlanGate:
        scope = "resource:read" if operation in ("list", "get") else "resource:write"
        return PlanGate(settings.get("policy") or DEFAULT_POLICY, engine=state.engine, scope=scope)

    # ── list ─────────────────────────────────────────────────────────────
    settings = operation_settings(resource, "list")
    if settings:
        page_model = None
        if not resource.transformer:
            page_model = create_model(
                f"{title}Page",
                __base__=BaseModel,
                data=(list[read_model], ...),
                page=(int, ...),
                per_page=(int, ...),
                total=(int | None, None),
            )

        async def list_records(
            ctx: HttpContext,
            page=Query(1, type=int, ge=1, description="Page number, from 1"),
            per_page=Query(20, type=int, ge=1, le=MAX_PAGE_SIZE, description="Records per page"),
            sort=Query(
                None, type=str, description="Comma-separated fields; prefix with - for descending"
            ),
            select=Query(None, type=str, description="Comma-separated fields to return"),
            expand=Query(None, type=str, description="Comma-separated relations to include"),
        ):
            plan = ctx.scope[PLAN_SCOPE_KEY]
            store = await state.store(resource.name)
            filters = parse_filters(ctx.query_params, spec)
            filters += [Filter(column, "eq", value) for column, value in plan.filters.items()]
            columns = [c.strip() for c in select.split(",") if c.strip()] if select else None
            if columns and spec.primary_key not in columns:
                columns.append(spec.primary_key)
            key = None
            if resource.cache_ttl:
                key = f"res:{resource.name}:list:{cache_key(ctx, sorted(ctx.query_params.items()))}"
                cached = await platform.cache_get(state, key)
                if cached is not None:
                    return cached
            rows, total = await store.list(
                filters=filters,
                sort=parse_sort(sort, spec),
                limit=per_page,
                offset=(page - 1) * per_page,
                select=columns,
            )
            if plan.residual is not None:
                context = build_policy_context(ctx)
                rows = [
                    row
                    for row in rows
                    if await state.engine.row_allowed(plan, {**context, "record": row})
                ]
                total = None  # a per-row policy makes the database total unreliable
            if expand:
                await _expand(
                    state, spec, rows, [name.strip() for name in expand.split(",") if name.strip()]
                )
            body = {
                "data": await _present(state, resource, ctx, rows),
                "page": page,
                "per_page": per_page,
                "total": total,
            }
            if key:
                await platform.cache_set(
                    state, key, body, ttl=resource.cache_ttl, tags=[resource_tag(resource.name)]
                )
            return body

        app.get(
            base,
            handler=list_records,
            name=f"{resource.name}.list",
            summary=f"List {resource.name}",
            description=resource.description or None,
            tags=tags,
            auth=gate("list", settings),
            response_model=page_model,
            middleware=limits,
        )
        registered.append(f"GET {base}")

    # ── get ──────────────────────────────────────────────────────────────
    settings = operation_settings(resource, "get")
    if settings:

        async def get_record(
            ctx: HttpContext,
            id: str,
            expand=Query(None, type=str, description="Comma-separated relations to include"),
        ):
            plan = ctx.scope[PLAN_SCOPE_KEY]
            record_id = _coerce_id(spec, id)
            key = None
            if resource.cache_ttl:
                key = f"res:{resource.name}:get:{cache_key(ctx, id, expand)}"
                cached = await platform.cache_get(state, key)
                if cached is not None:
                    return cached
            store = await state.store(resource.name)
            record = await store.get(record_id)
            if record is None or any(str(record.get(k)) != str(v) for k, v in plan.filters.items()):
                raise HTTPException(status_code=404, detail="Not found")
            if plan.residual is not None and not await state.engine.row_allowed(
                plan, {**build_policy_context(ctx), "record": record}
            ):
                raise HTTPException(status_code=404, detail="Not found")
            if expand:
                await _expand(
                    state,
                    spec,
                    [record],
                    [name.strip() for name in expand.split(",") if name.strip()],
                )
            body = await _present(state, resource, ctx, record)
            if key:
                await platform.cache_set(
                    state, key, body, ttl=resource.cache_ttl, tags=[resource_tag(resource.name)]
                )
            return body

        app.get(
            item,
            handler=get_record,
            name=f"{resource.name}.get",
            summary=f"Get one {resource.name} record",
            tags=tags,
            auth=gate("get", settings),
            response_model=None if resource.transformer else read_model,
            middleware=limits,
        )
        registered.append(f"GET {item}")

    # ── create ───────────────────────────────────────────────────────────
    settings = operation_settings(resource, "create")
    if settings:
        create_model_ = compile_model(
            f"{title}Create", spec.fields, mode="create", registry=registry
        )
        policy = settings.get("policy") or DEFAULT_POLICY

        async def create_record(ctx: HttpContext, body):
            # Defaults count as values; unset optional fields are simply absent.
            full = body.model_dump(mode="json")
            data = {k: v for k, v in full.items() if k in body.model_fields_set or v is not None}
            if resource.owner_field:
                actor = _auth_actor(ctx)
                if actor is not None:
                    data[resource.owner_field] = actor
                elif not _is_service(ctx):
                    raise HTTPException(status_code=401, detail="Authentication required")
            decision = await state.engine.check(
                policy, build_policy_context(ctx, record=data, input=data)
            )
            if not decision:
                raise HTTPException(status_code=403, detail=decision.reason)
            store = await state.store(resource.name)
            record = await store.create(data)
            await after_write(
                platform,
                state,
                resource.name,
                "created",
                record,
                actor=_auth_actor(ctx),
                request_id=_request_id(ctx),
            )
            note("resource", f"{resource.name}.created")
            return json_response(await _present(state, resource, ctx, record), status_code=201)

        app.post(
            base,
            handler=create_record,
            name=f"{resource.name}.create",
            summary=f"Create a {resource.name} record",
            tags=tags,
            request_model=create_model_,
            auth=gate("create", settings),
            middleware=limits,
        )
        registered.append(f"POST {base}")

    # ── update ───────────────────────────────────────────────────────────
    settings = operation_settings(resource, "update")
    if settings:
        update_model = compile_model(
            f"{title}Update", spec.fields, mode="update", registry=registry
        )
        policy = settings.get("policy") or DEFAULT_POLICY

        async def update_record(ctx: HttpContext, id: str, body):
            data = body.model_dump(exclude_unset=True, mode="json")
            if resource.owner_field:
                data.pop(resource.owner_field, None)
            record_id = _coerce_id(spec, id)
            store = await state.store(resource.name)
            existing = await store.get(record_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Not found")
            decision = await state.engine.check(
                policy, build_policy_context(ctx, record=existing, input=data)
            )
            if not decision:
                raise HTTPException(
                    status_code=404 if not _auth_actor(ctx) else 403, detail=decision.reason
                )
            record = await store.update(record_id, data)
            await after_write(
                platform,
                state,
                resource.name,
                "updated",
                record,
                actor=_auth_actor(ctx),
                request_id=_request_id(ctx),
            )
            return await _present(state, resource, ctx, record)

        for method in ("patch", "put"):
            getattr(app, method)(
                item,
                handler=update_record,
                name=f"{resource.name}.update.{method}",
                summary=f"Update a {resource.name} record",
                tags=tags,
                request_model=update_model,
                auth=gate("update", settings),
                middleware=limits,
                exclude_from_schema=method == "put",
            )
        registered.append(f"PATCH {item}")

    # ── delete ───────────────────────────────────────────────────────────
    settings = operation_settings(resource, "delete")
    if settings:
        policy = settings.get("policy") or DEFAULT_POLICY

        async def delete_record(ctx: HttpContext, id: str):
            record_id = _coerce_id(spec, id)
            store = await state.store(resource.name)
            existing = await store.get(record_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Not found")
            decision = await state.engine.check(policy, build_policy_context(ctx, record=existing))
            if not decision:
                raise HTTPException(
                    status_code=404 if not _auth_actor(ctx) else 403, detail=decision.reason
                )
            await store.delete(record_id)
            await after_write(
                platform,
                state,
                resource.name,
                "deleted",
                existing,
                actor=_auth_actor(ctx),
                request_id=_request_id(ctx),
            )
            return no_content()

        app.delete(
            item,
            handler=delete_record,
            name=f"{resource.name}.delete",
            summary=f"Delete a {resource.name} record",
            tags=tags,
            auth=gate("delete", settings),
            middleware=limits,
        )
        registered.append(f"DELETE {item}")

    return registered
