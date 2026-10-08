"""An environment's effective infrastructure and limits, and where each came from.

Studio shows this so nobody has to guess which variable is in effect: for the data database,
storage, mail and each limit, whether the value comes from the environment's own ``<ENV>_*``
variable, from the deployment-wide ``PAWABASE_*`` one, from an older install's stored ``infra``
(deprecated), or is the default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.storage.manager import ENVIRONMENT_STORAGE
from pawabase_core import envvars

if TYPE_CHECKING:
    from app.platform import Platform
    from app.state import EnvironmentState

#: Limit variable key, and the settings attribute holding the deployment-wide value (``None``
#: where only the gateway knows it).
LIMITS = (
    ("MAX_USERS", "max_users"),
    ("MAX_API_KEYS_PER_ENVIRONMENT", "max_api_keys_per_environment"),
    ("MAX_UPLOAD_BYTES", "max_upload_bytes"),
    ("DAILY_REQUEST_LIMIT", "daily_request_limit"),
    ("MONTHLY_REQUEST_LIMIT", "monthly_request_limit"),
    ("MAX_ACTIVE_CONNECTIONS", "max_active_connections"),
    ("RATE_LIMIT", None),
    ("RATE_WINDOW", None),
)


def limit_rows(platform: Platform, env: str) -> list[dict[str, Any]]:
    prefix = envvars.prefix_for(env)
    settings = platform.settings
    rows = []
    for key, attribute in LIMITS:
        own = envvars.get(env, key)
        if own is not None:
            value, source = own, "environment"
        elif attribute is not None:
            value = getattr(settings, attribute)
            default = type(settings).model_fields[attribute].default
            source = "deployment" if value != default else "default"
        else:
            value, source = None, "default"  # a gateway setting: the gateway applies it
        rows.append(
            {
                "key": key,
                "value": value,
                "source": source,
                "variable": f"{prefix}_{key}",
                "global_variable": f"PAWABASE_{key}",
            }
        )
    return rows


def report(platform: Platform, state: EnvironmentState) -> dict[str, Any]:
    env = state.env_name
    prefix = envvars.prefix_for(env)
    storage = platform.storage._config(state)
    storage_source = (
        "environment"
        if any(envvars.get(env, key) is not None for key in ENVIRONMENT_STORAGE)
        else "stored"
        if state.infra.get("storage")
        else "default"
    )
    mail = platform.mail.describe(state)
    return {
        "database": {"source": state.database_source(), "variable": f"{prefix}_DATA_URL"},
        "storage": {
            "driver": storage.get("driver", "local"),
            "source": storage_source,
            "variable": f"{prefix}_STORAGE_*",
        },
        "mail": {
            "configured": mail["configured"],
            "suppressed": mail["suppressed"],
            "variable": f"{prefix}_MAIL_*",
        },
        "limits": limit_rows(platform, env),
        "pool": {
            "min": platform.settings.db_pool_min,
            "max": platform.settings.db_pool_max,
        },
        "variables": {"environment_prefix": f"{prefix}_", "global_prefix": "PAWABASE_"},
    }
