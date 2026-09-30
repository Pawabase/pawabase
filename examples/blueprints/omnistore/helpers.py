"""Shared builders for the OmniStore commerce blueprint.

Every module builds plain data; ``build.py`` assembles the document that Studio
imports. The helpers mirror Pawabase's stored shapes exactly:

* conditions — the policy condition language (``all``/``any``/``not`` + operators);
* resources  — the ``ResourceBody`` shape;
* flows      — ``{"nodes": [...], "edges": [...]}`` in the ``@xyflow/react`` shape;
* routes     — the ``RouteBody`` shape.

Conventions used across the modules:

* money is integer minor units (``*_minor``);
* percentages and rates are basis points (``*_bps``) so rounding is explicit;
* stock is never written directly: ``stock_levels.on_hand`` and
  ``stock_levels.reserved`` move together and every change is journalled in
  ``inventory_ledger``;
* a *store* is an Akountz organization, so a store-owned row carries
  ``store_id`` and store rules push down to ``store_id = $auth.org``;
* every flow a client reaches ends in ``response.return``; flows reached by
  events, schedules or webhooks end in a state write or an emit.
"""

from __future__ import annotations

from collections import defaultdict, deque

# ── fields ───────────────────────────────────────────────────────────────────


def F(name, type="string", **kw):
    """A field definition."""
    return {"name": name, "type": type, **kw}


def money(name, **kw):
    """An integer minor-unit amount (kobo, cents)."""
    return F(name, "integer", minimum=0, **kw)


def bps(name, **kw):
    """A basis-point rate (750 = 7.5%)."""
    return F(name, "integer", minimum=0, maximum=10000, **kw)


def ts(name, **kw):
    """An epoch-second field, for deadline arithmetic that stays dialect safe."""
    return F(name, "integer", **kw)


SLUG = r"^[a-z0-9][a-z0-9-]{1,62}$"
HANDLE = r"^[a-z0-9][a-z0-9-]{0,120}$"
CODE = r"^[A-Z0-9][A-Z0-9_-]{1,30}$"
CURRENCY = r"^[A-Z]{3}$"
COUNTRY = r"^[A-Z]{2}$"
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# ── conditions ───────────────────────────────────────────────────────────────


def ALL(*conds):
    return {"all": [c for c in conds]}


def ANY(*conds):
    return {"any": [c for c in conds]}


def NOT(cond):
    return {"not": cond}


def EQ(a, b):
    return {"eq": [a, b]}


def NE(a, b):
    return {"ne": [a, b]}


def GT(a, b):
    return {"gt": [a, b]}


def GTE(a, b):
    return {"gte": [a, b]}


def LT(a, b):
    return {"lt": [a, b]}


def LTE(a, b):
    return {"lte": [a, b]}


def IN(path, values):
    return {"in": [path, list(values)]}


def NOT_IN(path, values):
    return {"not_in": [path, list(values)]}


def EXISTS(path):
    return {"exists": path}


def TRUTHY(path):
    return {"truthy": path}


def EMPTY(path):
    return {"empty": path}


def OWNER(field="user_id"):
    """The signed-in user owns the record."""
    return {"owner": field}


def AUTHENTICATED():
    return {"authenticated": True}


def ROLE(*names):
    return {"role": list(names)}


def KIND(kind):
    return {"kind": kind}


def ORG(role=None):
    """Staff of the active organization, optionally with one of *role*."""
    conds = [EXISTS("$auth.org")]
    if role:
        conds.append(IN("$auth.org_role", role if isinstance(role, list) else [role]))
    return ALL(*conds)


# ── resources ────────────────────────────────────────────────────────────────


def ops(list_=None, get=None, create=None, update=None, delete=None):
    """Operation settings; a policy of ``False`` disables the operation."""
    spec = {"list": list_, "get": get, "create": create, "update": update, "delete": delete}
    return {
        op: (
            {"enabled": False, "policy": None}
            if policy is False
            else {"enabled": policy is not None, "policy": policy}
        )
        for op, policy in spec.items()
    }


def BT(name, resource, field):
    return {"name": name, "type": "belongs_to", "resource": resource, "field": field}


def HM(name, resource, field):
    return {"name": name, "type": "has_many", "resource": resource, "field": field}


def resource(name, description, fields, operations, *, relations=(), tags=(), owner=None, **extra):
    """A ResourceBody, with every optional knob defaulted explicitly."""
    return {
        "name": name,
        "description": description,
        "id_type": "integer",
        "fields": fields,
        "operations": operations,
        "relations": list(relations),
        "owner_field": owner,
        "timestamps": True,
        "events": extra.pop("events", True),
        "realtime": extra.pop("realtime", False),
        "cache_ttl": extra.pop("cache_ttl", 0),
        "rate_limit": extra.pop("rate_limit", {}),
        "transformer": extra.pop("transformer", None),
        "tags": list(tags),
        **extra,
    }


# ── flows ────────────────────────────────────────────────────────────────────


def flow(name, description, nodes, edges, timeout=60, record_runs=True):
    """A FlowBody.

    Args:
        nodes: ``(id, block, config)`` triples. Laid out top-down by edge depth so
            Studio's canvas opens readable.
        edges: ``(source, target)`` or ``(source, target, handle)`` tuples.
    """
    children, indegree = defaultdict(list), defaultdict(int)
    for edge in edges:
        children[edge[0]].append(edge[1])
        indegree[edge[1]] += 1
    depth: dict[str, int] = {}
    queue = deque((node[0], 0) for node in nodes if indegree[node[0]] == 0)
    while queue:
        node_id, distance = queue.popleft()
        if depth.get(node_id, -1) >= distance:
            continue
        depth[node_id] = distance
        queue.extend((child, distance + 1) for child in children[node_id])
    columns: dict[int, int] = defaultdict(int)
    out_nodes = []
    for node_id, block, config in nodes:
        distance = depth.get(node_id, 0)
        out_nodes.append(
            {
                "id": node_id,
                "position": {"x": 60 + columns[distance] * 300, "y": 40 + distance * 160},
                "data": {"block": block, "config": config},
            }
        )
        columns[distance] += 1
    out_edges = []
    for edge in edges:
        handle = edge[2] if len(edge) > 2 else "next"
        out_edges.append(
            {
                "id": f"{edge[0]}-{handle}-{edge[1]}",
                "source": edge[0],
                "target": edge[1],
                **({"sourceHandle": handle} if handle != "next" else {}),
            }
        )
    return {
        "name": name,
        "description": description,
        "definition": {"nodes": out_nodes, "edges": out_edges},
        "enabled": True,
        "timeout": timeout,
        "record_runs": record_runs,
    }


def route(
    method,
    path,
    name,
    description,
    policy,
    handler,
    *,
    tags,
    input_schema=None,
    input_fields=None,
    rate=None,
    cache=0,
    handler_type="flow",
):
    """A RouteBody bound to a flow (or a Python function)."""
    return {
        "method": method,
        "path": path,
        "name": name,
        "description": description,
        "policy": policy,
        "input_schema": input_schema,
        "input_fields": input_fields,
        "response_schema": None,
        "transformer": None,
        "handler_type": handler_type,
        "handler": handler,
        "rate_limit": rate or {},
        "cache_ttl": cache,
        "tags": tags,
        "enabled": True,
    }


# ── templates ────────────────────────────────────────────────────────────────


def body(field):
    """A custom route's validated request body field."""
    return "{{ input.body.%s }}" % field


def param(name):
    """A custom route's path parameter."""
    return "{{ input.params.%s }}" % name


def query(name, default=None):
    """A custom route's query parameter, with an optional fallback.

    Pawabase templates are path lookups with a small filter set, not Jinja:
    the fallback must be the ``default`` filter, never an ``or`` expression.
    """
    if default is None:
        return "{{ input.query.%s }}" % name
    return '{{ input.query.%s | default:"%s" }}' % (name, default)


def step(*path):
    """A finished block's output."""
    return "{{ steps.%s }}" % ".".join(str(part) for part in path)


def payload(field):
    """A field of the event (or webhook) payload that triggered the run."""
    return "{{ input.event.payload.%s }}" % field


def rec(field):
    """A field of the resource record carried by a resource event."""
    return "{{ input.event.payload.record.%s }}" % field


# ── node shortcuts ───────────────────────────────────────────────────────────
#
# A node is an ``(id, block, config)`` triple. These builders keep flow bodies
# readable: a block's config keys appear as keyword arguments, so a reader sees
# what each step does without opening the registry.


def N(node_id, block_key, **config):
    """A flow node. ``node_id``/``block_key`` stay clear of config keys like ``id``."""
    return (node_id, block_key, config)


def TRIGGER_HTTP(id, method, path, policy):
    """Starts a run when a custom route is called."""
    return N(id, "trigger.http", method=method, path=path, policy=policy)


def TRIGGER_EVENT(id, event):
    return N(id, "trigger.event", event=event)


def TRIGGER_RESOURCE(id, resource_name, operations=("create", "update")):
    return N(id, "trigger.resource", resource=resource_name, operations=list(operations))


def TRIGGER_SCHEDULE(id, every=None, cron=None):
    return N(id, "trigger.schedule", every=every, cron=cron)


def TRIGGER_WEBHOOK(id, hook):
    return N(id, "trigger.webhook", hook=hook)


def GET(id, resource_name, id_expr):
    return N(id, "resource.get", resource=resource_name, id=id_expr)


def LIST(id, resource_name, filters, limit=20, sort=None):
    return N(id, "resource.list", resource=resource_name, filters=filters, limit=limit, sort=sort)


def CREATE(id, resource_name, data):
    return N(id, "resource.create", resource=resource_name, data=data)


def UPDATE(id, resource_name, id_expr, data):
    return N(id, "resource.update", resource=resource_name, id=id_expr, data=data)


def TX(id, operations):
    """Every operation in one SQL transaction: all of it commits, or none does."""
    return N(id, "db.transaction", operations=list(operations))


def OP_UPDATE(resource_name, id_expr, data):
    return {"op": "update", "resource": resource_name, "id": id_expr, "data": data}


def OP_CREATE(resource_name, data):
    return {"op": "create", "resource": resource_name, "data": data}


def QUERY(id, sql, params=None):
    return N(id, "db.query", sql=sql, params=list(params or []))


def IF(id, condition):
    return N(id, "control.if", condition=condition)


def SWITCH(id, value, cases):
    return N(id, "control.switch", value=value, cases=cases)


def FOREACH(id, items, concurrency=1):
    return N(id, "control.foreach", items=items, concurrency=concurrency)


def SET(id, **values):
    """Define ``vars.*`` for later templates — the place to name a value once."""
    return N(id, "control.set", values=values)


def CALC(id, operation, values, digits=0):
    """Integer-safe arithmetic: add, subtract, multiply, divide, max, min, round."""
    return N(id, "math.calculate", operation=operation, values=list(values), digits=digits)


def NOW(id, offset=0, format="iso"):
    return N(id, "time.now", offset_seconds=offset, format=format)


def ID_TOKEN(id, kind="token", length=24):
    return N(id, "util.id", kind=kind, length=length)


def CHECK(id, value, fields, on_invalid=None):
    """Validate a payload against a field list; ``invalid`` handle on failure."""
    config = {"value": value, "fields": fields}
    if on_invalid:
        config["on_invalid"] = on_invalid
    return N(id, "validate.schema", **config)


def GUARD(id, policy, record=None, input_=None):
    """Ask a named policy about a record; ``allowed`` / ``denied`` handles."""
    config = {"policy": policy}
    if record is not None:
        config["record"] = record
    if input_ is not None:
        config["input"] = input_
    return N(id, "policy.check", **config)


def REQUIRE(id, role=None, permission=None):
    return N(id, "auth.require", role=role, permission=permission)


def CACHE_GET(id, key):
    return N(id, "cache.get", key=key)


def CACHE_SET(id, key, value, ttl=60, tags=None):
    return N(id, "cache.set", key=key, value=value, ttl=ttl, **({"tags": list(tags)} if tags else {}))

def PICK(id, value, fields):
    return N(id, "transform.pick", value=value, fields=list(fields))


def TEMPLATE(id, template):
    return N(id, "transform.template", template=template)


def MAIL(id, to, template, data=None, subject=None):
    config = {"to": to, "template": template}
    if subject:
        config["subject"] = subject
    if data is not None:
        config["data"] = data
    return N(id, "mail.send", **config)


def NOTIFY(id, store_id, user_id, kind, title, body, link="", severity="info"):
    """Write a notification row; the staff inbox and app read it live."""
    return CREATE(
        id,
        "notifications",
        {
            "store_id": store_id,
            "user_id": user_id,
            "kind": kind,
            "title": title,
            "body": body,
            "link": link,
            "severity": severity,
        },
    )


def ORDER_EVENT(id, order_id, store_id, kind, message, actor_id=None, actor_kind="system", data=None):
    """Append to the order's timeline."""
    return CREATE(
        id,
        "order_events",
        {
            "store_id": store_id,
            "order_id": order_id,
            "kind": kind,
            "message": message,
            "actor_id": actor_id,
            "actor_kind": actor_kind,
            "data": data or {},
        },
    )


def LEDGER(id, row, delta, reason, reference_type=None, reference_id=None, actor=None, note=None, kind="staff"):
    """Journal a stock movement. *row* is a stock_levels row; *delta* is signed."""
    return CREATE(
        id,
        "inventory_ledger",
        {
            "store_id": f"{{{{ {row}.store_id }}}}",
            "warehouse_id": f"{{{{ {row}.warehouse_id }}}}",
            "variant_id": f"{{{{ {row}.variant_id }}}}",
            "delta": delta,
            "reason": reason,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "on_hand_after": f"{{{{ {row}.on_hand }}}}",
            "reserved_after": f"{{{{ {row}.reserved }}}}",
            "note": note,
            "actor_id": actor,
            "actor_kind": kind,
        },
    )


def EMIT(id, event, payload):
    """Publish a platform event: webhooks, subscriptions and other flows hear it."""
    return N(id, "event.emit", event=event, payload=payload)


def PUBLISH(id, channel, event, payload):
    """Push to a realtime channel the client is subscribed to."""
    return N(id, "realtime.publish", channel=channel, event=event, payload=payload)


def WEBHOOK(id, event, payload):
    """Send to the store's outbound webhook endpoints (retried with backoff)."""
    return N(id, "webhook.send", event=event, payload=payload)


def HTTP(id, url, method="post", body=None, headers=None, timeout=10, retries=2):
    return N(id, "http.request", url=url, method=method, body=body or {}, headers=headers or {}, timeout=timeout, retries=retries)


def SECRET(id, name, as_="secret"):
    return N(id, "secret.get", name=name, **{"as": as_})


def QUEUE(id, flow_name, input=None, delay=0, queue=None):
    config = {"flow": flow_name, "delay": delay}
    if input is not None:
        config["input"] = input
    if queue:
        config["queue"] = queue
    return N(id, "queue.flow", **config)


def METRIC(id, name, value=1, tags=None):
    return N(id, "metric.increment", name=name, value=value, **({"tags": tags} if tags else {}))


def LOG(id, message, level="info", data=None, category="omnistore"):
    config = {"level": level, "message": message, "category": category}
    if data is not None:
        config["data"] = data
    return N(id, "log.write", **config)


def REPLY(id, body, status=200):
    """End a client-facing run with this body."""
    return N(id, "response.return", status=status, body=body)


def FAIL(id, status, code, message):
    """End a run by raising an API error."""
    return N(id, "error.raise", status=status, code=code, message=message)


def DONE(id, body=None):
    """End a background run: nothing to return, but keep the run record."""
    return N(id, "response.return", status=200, body=body if body is not None else {"ok": True})


def chain(edges, seq):
    """Link a linear run of nodes, in order."""
    for source, target in zip(seq, seq[1:]):
        edges.append((source, target))
    return edges


def out(id, *tail):
    """``steps.<id>.<tail>`` — the commonest template there is."""
    return "{{ steps.%s.%s }}" % (id, ".".join(str(part) for part in tail))


def sref(step_id, *tail):
    """``$steps.<id>.<tail>`` — the form a *condition* needs.

    ``out()`` renders a template; a condition compares values, so it needs the
    lookup path instead. Using one for the other silently compares a string.
    """
    return "$steps.%s%s" % (step_id, "".join(".%s" % part for part in tail))


def var(name, *tail):
    """``vars.<name>.<tail>`` inside a template."""
    return "{{ vars.%s%s }}" % (name, "".join(".%s" % part for part in tail))


def vref(name):
    """``$vars.<name>`` inside a condition."""
    return "$vars.%s" % name

