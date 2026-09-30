"""Documents apikey auth, and required project/environment scoping, on an
OpenAPI spec built by Sillo (``compiled.build_openapi(...)``).

Every apikey-authenticated request must carry ``?project_id=&environment=``:
the gateway rejects one that doesn't the same way it rejects a missing apikey
(see ``GatewayProxy._context`` in the gateway service). This module is the
single place that documents that requirement, so the generated OpenAPI specs
(operator docs, public docs) can't drift from what the gateway enforces.
"""

from __future__ import annotations

from typing import Any

#: Required on every apikey-authenticated operation.
SCOPE_QUERY_PARAMS: list[dict[str, Any]] = [
    {
        "name": "project_id",
        "in": "query",
        "required": True,
        "schema": {"type": "string"},
        "description": "The project's ref.",
    },
    {
        "name": "environment",
        "in": "query",
        "required": True,
        "schema": {"type": "string"},
        "description": "The environment name, e.g. development or production.",
    },
]


def add_apikey_security(spec_dict: dict[str, Any]) -> dict[str, Any]:
    """Add the apikey security scheme, and project_id/environment as required
    query params, to every operation in an OpenAPI spec."""
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
            "Requires ?project_id=<ref>&environment=<env> to scope resolution to this project/environment."
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
