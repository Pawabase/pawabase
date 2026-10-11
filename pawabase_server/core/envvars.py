"""Per-environment configuration, read from environment variables.

Infrastructure (databases, storage, mail) and limits belong to the *deployment*, so
they come from the process environment and not from rows an API call can edit. Every
setting has one global variable, ``PAWABASE_<KEY>``, and a Pawabase environment can
override it with ``<ENVIRONMENT>_<KEY>``::

    PAWABASE_MAIL_HOST=smtp.example.com        # every environment
    PRODUCTION_MAIL_HOST=smtp.sendgrid.net     # the "production" environment only
    STAGING_MAX_USERS=50                       # the "staging" environment only

The environment's name is upper-cased and anything that is not a letter or a digit
becomes ``_``, so ``my-env`` reads ``MY_ENV_<KEY>``. Because two names can normalise to
the same prefix, :func:`name_problem` is what environment creation uses to refuse a name
that would read another environment's variables.

Only the keys in :data:`KEYS` are looked up. That list is also what :func:`unrecognised`
checks a deployment's variables against, so a typo such as ``PRODUCTION_STORAGE_BUKET`` is
reported instead of being ignored.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

#: The prefix of every global variable.
GLOBAL_PREFIX = "PAWABASE_"

#: Settings an environment may override, with what each is for.
KEYS: dict[str, str] = {
    "DATA_URL": "Where the environment's resource data lives (a full database URL).",
    "STORAGE_DRIVER": "local, s3 or memory.",
    "STORAGE_ROOT": "Local storage directory.",
    "STORAGE_ENDPOINT": "S3-compatible endpoint.",
    "STORAGE_PUBLIC_ENDPOINT": "Where browsers reach the endpoint, for presigned URLs.",
    "STORAGE_BUCKET": "Remote bucket.",
    "STORAGE_REGION": "Bucket region.",
    "STORAGE_ACCESS_KEY": "S3 access key.",
    "STORAGE_SECRET_KEY": "S3 secret key.",
    "STORAGE_PREFIX": "Key prefix inside the bucket.",
    "STORAGE_PATH_STYLE": "Address the bucket in the URL path (MinIO, Ceph).",
    "MAIL_HOST": "SMTP host. Without one, mail is logged and not sent.",
    "MAIL_PORT": "SMTP port (587 by default).",
    "MAIL_USERNAME": "SMTP username.",
    "MAIL_PASSWORD": "SMTP password.",
    "MAIL_USE_SSL": "Connect with SSL (the default on port 465).",
    "MAIL_USE_TLS": "Upgrade with STARTTLS (the default on port 587).",
    "MAIL_FROM": "Default sender address.",
    "MAIL_REPLY_TO": "Default reply-to address.",
    "MAIL_SUPPRESS": "Log mail but never send it.",
    "FUNCTION_ISOLATION": "Where functions run: inprocess, or process (a worker process per deployment, with limits).",
    "MAX_USERS": "Most active application users.",
    "MAX_API_KEYS_PER_ENVIRONMENT": "Most active API keys.",
    "MAX_UPLOAD_BYTES": "Largest single object upload.",
    "DAILY_REQUEST_LIMIT": "Requests allowed per UTC day. 0 disables the limit.",
    "MONTHLY_REQUEST_LIMIT": "Requests allowed per UTC month. 0 disables the limit.",
    "MAX_ACTIVE_CONNECTIONS": "Most simultaneous WebSocket connections.",
    "RATE_LIMIT": "Requests per window, per key or client address.",
    "RATE_WINDOW": "The rate-limit window, in seconds.",
}

_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")
#: The families of setting names. A variable is only called a typo when it starts like
#: one of these, so an unrelated `POSTGRES_PASSWORD` is not flagged for an environment
#: that happens to be called `postgres`.
_FAMILIES = tuple(sorted({key.split("_", 1)[0] + "_" for key in KEYS}))
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def prefix_for(name: str) -> str:
    """The variable prefix of an environment: ``my-env`` becomes ``MY_ENV``."""
    return _NOT_ALNUM.sub("_", name).upper()


def _read(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def get(name: str, key: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The environment's own value for *key*, or ``None``.

    Only ``<ENVIRONMENT>_<KEY>`` is read: the global fallback is the caller's
    settings object, which already holds ``PAWABASE_<KEY>``. A variable set to an
    empty string counts as unset.
    """
    value = _read(environ).get(f"{prefix_for(name)}_{key}")
    return value if value not in (None, "") else None


def text(name: str, key: str, default: str = "", environ: Mapping[str, str] | None = None) -> str:
    value = get(name, key, environ)
    return default if value is None else value


def integer(name: str, key: str, default: int = 0, environ: Mapping[str, str] | None = None) -> int:
    """An integer setting. A value that is not a whole number is an error, not a silent default."""
    value = get(name, key, environ)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{prefix_for(name)}_{key} must be a whole number, got {value!r}") from exc


def boolean(
    name: str, key: str, default: bool = False, environ: Mapping[str, str] | None = None
) -> bool:
    value = get(name, key, environ)
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{prefix_for(name)}_{key} must be true or false, got {value!r}")


def overrides(name: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Every setting the environment overrides, by key."""
    return {key: value for key in KEYS if (value := get(name, key, environ)) is not None}


def name_problem(name: str, existing: Iterable[str] = ()) -> str | None:
    """Why *name* cannot be a new environment's name, or ``None``.

    Two names that normalise to the same prefix would read the same variables, and
    ``pawabase`` would read the global ones, so both are refused.
    """
    prefix = prefix_for(name)
    if prefix == GLOBAL_PREFIX.rstrip("_"):
        return f"{name!r} is reserved: its variables would be the global PAWABASE_* ones"
    for other in existing:
        if other != name and prefix_for(other) == prefix:
            return (
                f"{name!r} and {other!r} would both read {prefix}_* variables; "
                "choose a name that differs by more than punctuation or case"
            )
    return None


def unrecognised(
    names: Iterable[str], environ: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """Variables that look like an environment's override but name no known setting.

    For each environment, the variables that start with its prefix, continue like one of
    the setting families (``STORAGE_``, ``MAIL_``, ``MAX_`` ...) and are not in
    :data:`KEYS`. They are almost always typos.
    """
    environment = _read(environ)
    found: dict[str, list[str]] = {}
    for name in names:
        prefix = prefix_for(name) + "_"
        other_prefixes = {prefix_for(other) + "_" for other in names if other != name}
        bad = []
        for variable in environment:
            if not variable.startswith(prefix) or variable.startswith(GLOBAL_PREFIX):
                continue
            # `PROD_X` belongs to `prod`, not to a longer environment `prod_eu`.
            if any(
                variable.startswith(other) and len(other) > len(prefix) for other in other_prefixes
            ):
                continue
            suffix = variable[len(prefix) :]
            if suffix not in KEYS and suffix.startswith(_FAMILIES):
                bad.append(variable)
        if bad:
            found[name] = sorted(bad)
    return found
