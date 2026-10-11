"""Validation for an environment's ``settings`` section.

Studio and the management API both write ``settings``. Platform-owned keys are
checked here so a typo in a CIDR range or a timezone fails when it is saved,
not when the gateway reads it. Any other key is open-ended data that flows and
policies read as ``$settings.<key>``.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sillo.exceptions import HTTPException

#: Written by the platform, never by a client.
RESERVED = ("deletion_pending",)

LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
CUSTOM_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _fail(key: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"settings.{key}: {message}")


def _origin(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail("cors_origins", "origins must be strings")
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise _fail("cors_origins", f"{value!r} is not an origin like https://app.example.com")
    return f"{parts.scheme}://{parts.netloc}"


def _network(value: Any) -> str:
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError as exc:
        raise _fail("ip_allowlist", f"{value!r} is not an IP address or CIDR range") from exc


def _text(key: str, value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise _fail(key, "must be text")
    if len(value) > limit:
        raise _fail(key, f"at most {limit} characters")
    return value.strip()


def _maintenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail("maintenance", "must be an object")
    retry = value.get("retry_after", 300)
    if isinstance(retry, bool) or not isinstance(retry, int) or not 1 <= retry <= 86400:
        raise _fail("maintenance", "retry_after must be between 1 and 86400 seconds")
    return {
        "enabled": bool(value.get("enabled", False)),
        "message": _text("maintenance", value.get("message", ""), 300),
        "retry_after": retry,
        "allow_secret_keys": bool(value.get("allow_secret_keys", True)),
    }


def validate(patch: dict[str, Any]) -> dict[str, Any]:
    """Check and normalise a settings patch.

    Args:
        patch: The ``settings`` object from an environment update. A value of
            ``None`` clears a key and is passed through untouched.

    Returns:
        The patch with platform-owned values normalised.

    Raises:
        HTTPException: 422 naming the first setting that is invalid.
    """
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key in RESERVED:
            raise _fail(key, "is managed by the platform")
        if value is None:
            clean[key] = None
        elif key == "public_docs":
            clean[key] = bool(value)
        elif key == "cors_origins":
            if not isinstance(value, list) or len(value) > 50:
                raise _fail(key, "must be a list of at most 50 origins")
            clean[key] = sorted({_origin(item) for item in value})
        elif key == "ip_allowlist":
            if not isinstance(value, list) or len(value) > 100:
                raise _fail(key, "must be a list of at most 100 addresses or ranges")
            clean[key] = sorted({_network(item) for item in value})
        elif key == "timezone":
            try:
                ZoneInfo(_text(key, value, 64))
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise _fail(key, f"{value!r} is not an IANA timezone like Africa/Lagos") from exc
            clean[key] = value.strip()
        elif key == "currency":
            code = _text(key, value, 3).upper()
            if not CURRENCY.match(code):
                raise _fail(key, "must be a three-letter code like NGN")
            clean[key] = code
        elif key == "locale":
            if not isinstance(value, str) or not LOCALE.match(value.strip()):
                raise _fail(key, "must look like en or en-NG")
            clean[key] = value.strip()
        elif key == "description":
            clean[key] = _text(key, value, 280)
        elif key == "maintenance":
            clean[key] = _maintenance(value)
        elif key == "realtime":
            if not isinstance(value, dict):
                raise _fail(key, "must be an object")
            clean[key] = value
        elif not CUSTOM_KEY.match(key):
            raise _fail(key, "custom setting names use letters, digits and underscores")
        else:
            clean[key] = value
    return clean
