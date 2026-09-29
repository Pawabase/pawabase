"""Shared builders for the SwiftLine logistics blueprint.

Every module builds plain data; ``build.py`` assembles the document. The helpers
mirror Pawabase's stored shapes exactly:

* conditions  — the policy condition language (``all``/``any``/``not`` + ops)
* resources   — ``ResourceBody`` shape
* flows       — ``{"nodes": [...], "edges": [...]}`` in ``@xyflow/react`` shape
* routes      — ``RouteBody`` shape

Conventions used everywhere:

* money is integer minor units (``*_minor``);
* timeout arithmetic uses integer epoch seconds (``*_ts``) so it is dialect
  safe — ``time.now`` with ``format: unix`` produces the reference value;
* distances are planar approximations in kilometres: ``|dlat| * 111.32 +
  |dlng| * market.km_per_deg_lng`` (markets carry the longitude scale);
* races are arbitrated by unique-constrained claim rows.
"""

from __future__ import annotations

from collections import defaultdict, deque

# ── fields ───────────────────────────────────────────────────────────────────


def F(name, type="string", **kw):
    return {"name": name, "type": type, **kw}


def money(name, **kw):
    return F(name, "integer", minimum=0, **kw)


def lat(name="lat", **kw):
    return F(name, "number", minimum=-90, maximum=90, **kw)


def lng(name="lng", **kw):
    return F(name, "number", minimum=-180, maximum=180, **kw)


def ts(name, **kw):
    """An epoch-seconds field used for comparisons."""
    return F(name, "integer", **kw)


SLUG = r"^[a-z0-9][a-z0-9-]{1,62}$"

# ── conditions ───────────────────────────────────────────────────────────────


def ALL(*conds):
    return {"all": list(conds)}


def ANY(*conds):
    return {"any": list(conds)}


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


def IN(a, values):
    return {"in": [a, values]}


def NIN(a, values):
    return {"not_in": [a, values]}


def EXISTS(path):
    return {"exists": path}


def TRUTHY(path):
    return {"truthy": path}


def EMPTY(path):
    return {"empty": path}


def CONTAINS(a, b):
    return {"contains": [a, b]}


def AUTH():
    return {"authenticated": True}


def ROLE(*roles):
    return {"role": list(roles)}


def PERM(*perms):
    return {"permission": list(perms)}


def OWNER(field="user_id"):
    return {"owner": field}


def SCOPE(scope):
    return {"scope": scope}


def REC_EQ(field, value):
    return {"eq": [f"$record.{field}", value]}


def AUTH_EQ(field, auth_path):
    return {"eq": [f"$record.{field}", f"$auth.{auth_path}"]}


# ── resources ────────────────────────────────────────────────────────────────


def OPS(list_=None, get=None, create=None, update=None, delete=None):
    spec = {"list": list_, "get": get, "create": create, "update": update, "delete": delete}
    return {
        op: ({"enabled": True, "policy": p} if p is not False else {"enabled": False, "policy": None})
        for op, p in spec.items()
    }


def BT(name, resource, field):
    return {"name": name, "type": "belongs_to", "resource": resource, "field": field}


def HM(name, resource, field):
    return {"name": name, "type": "has_many", "resource": resource, "field": field}


def resource(name, description, fields, operations, *, relations=(), tags=(), owner=None, **extra):
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


def N(node_id, block, **config):
    """A node tuple: ``("load", "resource.get", resource="orders", id="{{ … }}")``."""
    return (node_id, block, config)


def E(source, target, handle=None):
    return (source, target, handle) if handle else (source, target)


def flow(name, description, nodes, edges, timeout=120, record=True):
    """nodes: [(id, block, config)]; edges: [(source, target, handle?)] — laid out top-down."""
    # ``control.if`` only exposes ``true`` and ``false``.  A two-item edge
    # normally means the block's ``next`` output, but that output does not
    # exist for an if node.  Treat an omitted handle as the false/fall-through
    # branch so generated graphs cannot be rejected by the flow validator.
    block_by_id = {node[0]: node[1] for node in nodes}
    edges = [
        (edge[0], edge[1], "false")
        if len(edge) == 2 and block_by_id.get(edge[0]) == "control.if"
        else edge
        for edge in edges
    ]
    children, indegree = defaultdict(list), defaultdict(int)
    for e in edges:
        children[e[0]].append(e[1])
        indegree[e[1]] += 1
    depth, queue = {}, deque([(n[0], 0) for n in nodes if indegree[n[0]] == 0])
    while queue:
        node, d = queue.popleft()
        if depth.get(node, -1) >= d:
            continue
        depth[node] = d
        queue.extend((c, d + 1) for c in children[node])
    columns = defaultdict(int)
    out_nodes = []
    for node_id, block, config in nodes:
        d = depth.get(node_id, 0)
        out_nodes.append(
            {
                "id": node_id,
                "position": {"x": 60 + columns[d] * 300, "y": 40 + d * 160},
                "data": {"block": block, "config": config},
            }
        )
        columns[d] += 1
    out_edges = [
        {
            "id": f"{e[0]}-{e[2] if len(e) > 2 and e[2] else 'next'}-{e[1]}",
            "source": e[0],
            "target": e[1],
            **({"sourceHandle": e[2]} if len(e) > 2 and e[2] else {}),
        }
        for e in edges
    ]
    return {
        "name": name,
        "description": description,
        "definition": {"nodes": out_nodes, "edges": out_edges},
        "enabled": True,
        "timeout": timeout,
        "record_runs": record,
    }


def ERR(id, status, code, message):
    return N(id, "error.raise", status=status, code=code, message=message)


def IF(id, condition):
    return N(id, "control.if", condition=condition)


def SET(id, **values):
    return N(id, "control.set", values=values)


def LOG(id, message, level="info", data=None):
    cfg = {"level": level, "message": message}
    if data is not None:
        cfg["data"] = data
    return N(id, "log.write", **cfg)


def REPLY(id, body, status=200):
    return N(id, "response.return", status=status, body=body)


def TIMELINE(id, subject_type, subject_id, kind, summary, data=None, actor="$auth.user_id"):
    """Append an immutable timeline entry for a business object."""
    payload = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "kind": kind,
        "summary": summary,
        "actor_id": actor,
        "data": data or {},
    }
    return N(id, "resource.create", resource="timeline_events", data=payload)


def NOTIFY_USER(id, user_id, kind, title, body, link=""):
    """Write a notification row; the notifications fanout subscription delivers it."""
    return N(
        id,
        "resource.create",
        resource="notifications",
        data={
            "user_id": user_id,
            "kind": kind,
            "title": title,
            "body": body,
            "link": link,
            "channel": "app",
        },
    )


def PUBLISH(id, channel, event, payload):
    return N(id, "realtime.publish", channel=channel, event=event, payload=payload)


def EMIT(id, event, payload):
    return N(id, "event.emit", event=event, payload=payload)


def WEBHOOK(id, event, payload):
    return N(id, "webhook.send", event=event, payload=payload)


def NOW(id, format="unix", offset=0):
    return N(id, "time.now", format=format, offset_seconds=offset)


def CALC(id, operation, values, digits=0):
    return N(id, "math.calculate", operation=operation, values=values, digits=digits)


# ── routes ───────────────────────────────────────────────────────────────────


def route(method, path, name, description, policy, handler, *, tags, input_schema=None, rate=None, cache=0):
    return {
        "method": method,
        "path": path,
        "name": name,
        "description": description,
        "policy": policy,
        "input_schema": input_schema,
        "input_fields": None,
        "response_schema": None,
        "transformer": None,
        "handler_type": "flow",
        "handler": handler,
        "rate_limit": rate or {},
        "cache_ttl": cache,
        "tags": tags,
        "enabled": True,
    }
