"""Documents apikey auth, and the optional environment hint, on an
OpenAPI spec built by Sillo (``compiled.build_openapi(...)``).

An API key belongs to one environment, so the gateway resolves the environment
from the key alone. A request may also carry ``?environment=``; the gateway
rejects one that names a different environment than the key's (see
``GatewayProxy._context`` in the gateway service). This module is the single
place that documents it, so the generated OpenAPI specs can't drift from what
the gateway enforces.
"""

from __future__ import annotations

from typing import Any

#: Accepted on every apikey-authenticated operation.
SCOPE_QUERY_PARAMS: list[dict[str, Any]] = [
    {
        "name": "environment",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": "The environment the key belongs to, e.g. development or production. Optional: a key names its own environment, and a different one is refused.",
    },
]


def add_apikey_security(spec_dict: dict[str, Any]) -> dict[str, Any]:
    """Add the apikey security scheme, and the optional environment query
    param, to every operation in an OpenAPI spec."""
    if "components" not in spec_dict:
        spec_dict["components"] = {}
    if "securitySchemes" not in spec_dict["components"]:
        spec_dict["components"]["securitySchemes"] = {}

    spec_dict["components"]["securitySchemes"]["apikey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "apikey",
        "description": (
            "API Key for service keys (sk_) or publishable keys (pk_). Include as: apikey: <your_key>. "
            "The key identifies its environment."
        ),
    }

    both_auth = [{"apikey": []}, {"bearer": []}]
    spec_dict["security"] = both_auth

    for path_item in spec_dict.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not (isinstance(operation, dict) and "operationId" in operation):
                continue
            operation["security"] = both_auth
            params = operation.setdefault("parameters", [])
            existing = {p.get("name") for p in params if isinstance(p, dict)}
            for scope_param in SCOPE_QUERY_PARAMS:
                if scope_param["name"] not in existing:
                    params.append(scope_param)

    return spec_dict
