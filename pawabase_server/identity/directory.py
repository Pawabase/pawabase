"""The directory: the rules an environment's people live under.

A directory is one environment's user store and everything that governs it: the attributes a person has, how they sign in, how strong a
password must be and how long it lives, how an account is recovered, which devices are known and trusted, the keys a person may create
for themselves, and the identity providers they may arrive from. The attribute list and the sign-in identifiers are in
:mod:`pawabase_server.identity.signup_form`, branding is :mod:`pawabase_server.identity.hosted` and providers are
:mod:`pawabase_server.identity.federation`; this module holds the policy sections and validates them.

Everything is optional: an environment that sets none of this behaves as it always did.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

#: ``section -> key -> (kind, default, low, high)``. ``kind`` is ``bool``, ``int`` or ``choice`` (``low`` then lists the choices).
SCHEMA: dict[str, dict[str, tuple[str, Any, Any, Any]]] = {
    "passwords": {
        "max_length": ("int", 128, 16, 1024),
        "min_unique": ("int", 0, 0, 32),
        "history": ("int", 0, 0, 24),
        "max_age_days": ("int", 0, 0, 3650),
        "forbid_personal": ("bool", False, None, None),
        "breach_check": ("bool", False, None, None),
    },
    "recovery": {
        "enabled": ("bool", True, None, None),
        "link_minutes": ("int", 60, 5, 1440),
        "revoke_sessions": ("bool", True, None, None),
        "notify_change": ("bool", True, None, None),
    },
    "devices": {
        "track": ("bool", True, None, None),
        "notify_new": ("bool", False, None, None),
        "remember_days": ("int", 0, 0, 365),
        "max_per_user": ("int", 0, 0, 100),
    },
    "api_keys": {
        "enabled": ("bool", False, None, None),
        "max_per_user": ("int", 10, 1, 100),
        "max_days": ("int", 365, 0, 3650),
        "scoped": ("bool", True, None, None),
    },
    "claims": {
        "mode": ("choice", "all", ("all", "listed"), None),
    },
}

DEFAULTS: dict[str, dict[str, Any]] = {
    section: {key: spec[1] for key, spec in keys.items()} for section, keys in SCHEMA.items()
}


def clean(raw: Any, *, strict: bool = False) -> dict[str, dict[str, Any]]:
    """The directory settings that may be stored. ``strict`` raises ``ValueError`` listing every problem; otherwise bad values are dropped."""
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for section, values in source.items():
        if section not in SCHEMA:
            problems.append(f"{section!r} is not a directory section")
            continue
        if not isinstance(values, dict):
            problems.append(f"{section} must be an object")
            continue
        kept: dict[str, Any] = {}
        for key, value in values.items():
            spec = SCHEMA[section].get(key)
            if spec is None:
                problems.append(f"{section}.{key} is not a setting")
                continue
            kind, _, low, high = spec
            if kind == "bool":
                if isinstance(value, bool):
                    kept[key] = value
                else:
                    problems.append(f"{section}.{key} must be true or false")
            elif kind == "int":
                if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                    kept[key] = value
                else:
                    problems.append(f"{section}.{key} must be a whole number from {low} to {high}")
            elif value in low:
                kept[key] = value
            else:
                problems.append(f"{section}.{key} must be one of {', '.join(low)}")
        if kept:
            out[section] = kept
    if strict and problems:
        raise ValueError("; ".join(problems))
    return out


def resolved(raw: Any) -> dict[str, dict[str, Any]]:
    """Every section with its defaults filled in."""
    merged = deepcopy(DEFAULTS)
    for section, values in clean(raw).items():
        merged[section].update(values)
    return merged


def section(raw: Any, name: str) -> dict[str, Any]:
    return resolved(raw)[name]
