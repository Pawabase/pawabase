"""``python -m app.export_config``: print stored infrastructure settings as environment variables.

Older installs kept database, storage and mail settings on each environment (``infra``). They
now come from environment variables, so moving over is a matter of copying lines. This reads
what is stored and prints the equivalent ``<ENV>_*`` variables::

    python -m app.export_config                 # every environment
    python -m app.export_config --env production

The output contains credentials: treat it like the ``.env`` it is meant to become. A password
stored as ``secret://NAME`` is printed as that reference, which is valid in a variable too.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from app.storage.manager import ENVIRONMENT_STORAGE
from pawabase_core import envvars

#: ``infra.mail`` field -> the variable key that replaces it.
MAIL_FIELDS = {
    "host": "MAIL_HOST",
    "port": "MAIL_PORT",
    "username": "MAIL_USERNAME",
    "password": "MAIL_PASSWORD",
    "use_ssl": "MAIL_USE_SSL",
    "use_tls": "MAIL_USE_TLS",
    "from": "MAIL_FROM",
    "reply_to": "MAIL_REPLY_TO",
    "suppress": "MAIL_SUPPRESS",
}
#: ``infra.storage`` field -> the variable key that replaces it.
STORAGE_FIELDS = {field: variable for variable, field in ENVIRONMENT_STORAGE.items()}


def _value(value: Any) -> str:
    text = str(value).lower() if isinstance(value, bool) else str(value)
    if text == "" or any(char in text for char in " #\"'$`\\"):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'
    return text


def render(environments: Iterable[tuple[str, Mapping[str, Any]]]) -> list[str]:
    """The ``.env`` lines for each ``(environment name, infra)`` pair that has anything stored."""
    lines: list[str] = []
    for name, infra in environments:
        prefix = envvars.prefix_for(name)
        block: list[str] = []
        if infra.get("database_url"):
            block.append(f"{prefix}_DATA_URL={_value(infra['database_url'])}")
        for field, value in (infra.get("storage") or {}).items():
            if field in STORAGE_FIELDS and value not in (None, ""):
                block.append(f"{prefix}_{STORAGE_FIELDS[field]}={_value(value)}")
        for field, value in (infra.get("mail") or {}).items():
            if field in MAIL_FIELDS and value not in (None, ""):
                block.append(f"{prefix}_{MAIL_FIELDS[field]}={_value(value)}")
        if block:
            lines += [f"# environment: {name}", *block, ""]
    return lines


async def stored_infra(only: str | None = None) -> list[tuple[str, Mapping[str, Any]]]:
    from sillo.record import DatabaseManager

    from app.config import ApiSettings
    from database.config import MODEL_MODULES, database_config
    from database.models import Environment

    settings = ApiSettings()
    database = DatabaseManager(database_config(settings)).register_models(*MODEL_MODULES)
    await database.init()
    try:
        query = Environment.filter(name=only) if only else Environment.all()
        return [(e.name, e.infra or {}) async for e in query.order_by("name")]
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.export_config", description=__doc__)
    parser.add_argument("--env", help="print one environment only")
    arguments = parser.parse_args(argv)
    lines = render(asyncio.run(stored_infra(arguments.env)))
    if not lines:
        print("# nothing stored on the environments: nothing to move", file=sys.stderr)
        return 0
    print("\n".join(lines).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
