"""Database connection references: how a project's database address is kept without a password sitting in a row, a log or a response.

``env:NAME``  the address is the process environment variable ``NAME``, set and rotated by whatever deploys the runtime.
``enc:…``     the address, sealed with the installation's master key.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from pawabase_server.core.crypto import SecretBox

SQLITE = ("sqlite://", "sqlite:")
POSTGRES = ("postgres://", "postgresql://")
MYSQL = ("mysql://",)
VARIABLE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


class RefError(ValueError):
    """A reference that cannot be turned into an address."""


def provider_of(url: str) -> str:
    if url.startswith(POSTGRES):
        return "postgres"
    if url.startswith(MYSQL):
        return "mysql"
    if url.startswith(SQLITE):
        return "sqlite"
    raise RefError("the database address must start with postgres://, mysql:// or sqlite://")


def seal_url(box: SecretBox, url: str) -> str:
    provider_of(url)
    return "enc:" + box.seal(url)


def env_ref(name: str) -> str:
    if not VARIABLE.match(name):
        raise RefError("a variable name is capital letters, digits and underscores")
    return f"env:{name}"


def resolve_ref(box: SecretBox, ref: str, environ: Mapping[str, str] | None = None) -> str:
    """The address a reference stands for. Raises :class:`RefError` when it cannot be read."""
    source = os.environ if environ is None else environ
    if ref.startswith("env:"):
        name = ref[4:]
        if not VARIABLE.match(name):
            raise RefError("the reference names an invalid variable")
        value = source.get(name, "")
        if not value:
            raise RefError(f"the environment variable {name} is not set")
        provider_of(value)
        return value
    if ref.startswith("enc:"):
        try:
            value = box.open(ref[4:])
        except Exception as exc:
            raise RefError(
                "the sealed address cannot be opened with this installation's key"
            ) from exc
        provider_of(value)
        return value
    raise RefError("a connection reference is env:NAME or enc:…")


def redact(url: str) -> str:
    """The address with its password hidden, safe to show or log."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unreadable address)"
    if not parts.password:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = f"{parts.username}:***@" if parts.username else ""
    return urlunsplit((parts.scheme, f"{user}{host}", parts.path, parts.query, parts.fragment))


def describe(ref: str, url: str | None = None) -> dict[str, str]:
    """What an API or the command line may say about a reference."""
    out = {"kind": "environment variable" if ref.startswith("env:") else "sealed"}
    if ref.startswith("env:"):
        out["variable"] = ref[4:]
    if url:
        out["address"] = redact(url)
    return out
