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
    """A custom route's query parameter, with an optional fallback."""
    if default is None:
        return "{{ input.query.%s }}" % name
    return "{{ input.query.%s or '%s' }}" % (name, default)


def step(*path):
    """A finished block's output."""
    return "{{ steps.%s }}" % ".".join(str(part) for part in path)


def payload(field):
    """A field of the event (or webhook) payload that triggered the run."""
    return "{{ input.event.payload.%s }}" % field


def rec(field):
    """A field of the resource record carried by a resource event."""
    return "{{ input.event.payload.record.%s }}" % field
