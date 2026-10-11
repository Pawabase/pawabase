"""``pawabase-server copy-data``: move environments' resource data into the database this installation is configured to use.

    pawabase-server copy-data --from 'sqlite:///data/envs/{env}.db' [--env NAME] [--dry-run]

``{env}`` in ``--from`` is each environment's name. The target is wherever each environment's data lives now (the default data URL, or its
own ``<ENV>_DATA_URL``), so point the configuration at the new database first, then run this once.
"""

from __future__ import annotations

import sys

from pawabase_server.core import envvars
from pawabase_server.data.copy import copy_resource
from pawabase_server.data.source import DataSource
from pawabase_server.data.store import ResourceSpec
from pawabase_server.db.database import database
from pawabase_server.db.models import Environment, Resource
from pawabase_server.environments.state import default_data_url
from pawabase_server.settings import Settings


def _arguments(argv: list[str]) -> tuple[str, str | None, bool]:
    origin, only, dry = "", None, False
    items = iter(argv)
    for item in items:
        if item == "--from":
            origin = next(items, "")
        elif item.startswith("--from="):
            origin = item.split("=", 1)[1]
        elif item == "--env":
            only = next(items, None)
        elif item == "--dry-run":
            dry = True
        else:
            raise SystemExit(f"unknown option {item!r}")
    if not origin:
        raise SystemExit(
            "usage: pawabase-server copy-data --from <url, with {env}> [--env NAME] [--dry-run]"
        )
    return origin, only, dry


async def run(argv: list[str], settings: Settings) -> int:
    origin, only, dry = _arguments(argv)

    def say(message: str) -> None:
        print(message, flush=True)

    failed = 0
    async with database(settings):
        environments = await Environment.all()
        for environment in environments:
            name = environment.name
            if only and name != only:
                continue
            if envvars.get(name, "DATA_URL") or (environment.infra or {}).get("database_url"):
                say(f"{name}: has a database of its own configured; not touched")
                continue
            target_url = settings.tuned(default_data_url(settings.default_data_url, name))
            source_url = origin.replace("{env}", name)
            if source_url == default_data_url(settings.default_data_url, name):
                say(f"{name}: the source and the target are the same database; skipped")
                continue
            say(f"{name}: {source_url.split('@')[-1]}  ->  {target_url.split('@')[-1]}")
            source = await DataSource(source_url, f"copy-from:{name}").connect()
            target = await DataSource(target_url, f"copy-to:{name}").connect()
            try:
                resources = await Resource.filter(environment_id=environment.id).all()
                for resource in resources:
                    spec = ResourceSpec.from_model(resource)
                    try:
                        report = await copy_resource(source, target, spec, dry_run=dry, log=say)
                    except Exception as exc:  # one table failing must not hide the others
                        failed += 1
                        say(f"  {spec.name}: FAILED {type(exc).__name__}: {exc}")
                        continue
                    note = (
                        f" (columns left behind: {', '.join(report['skipped_columns'])})"
                        if report.get("skipped_columns")
                        else ""
                    )
                    if report["status"] == "incomplete":
                        failed += 1
                    say(f"  {spec.name}: {report['status']}, {report['rows']} row(s){note}")
            finally:
                await source.close()
                await target.close()
    say(
        "dry run: nothing was written"
        if dry
        else ("finished with problems" if failed else "finished")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(run(sys.argv[1:], Settings())))
