"""The ``pawabase-server`` command.

=========  ==============================================================================
serve      run the server (the default); it also starts the worker and the scheduler as child processes unless
           PAWABASE_WORKER_MODE=external
worker     run the queue worker, as a process of its own
scheduler  run the scheduler, as a process of its own
migrate    apply pending database migrations; ``migrate make NAME`` writes a new one
check      validate the configuration and exit non-zero when something is wrong
copy-data  copy every environment's resource data from another database (e.g. SQLite files) into the configured one
version    print the version
=========  ==============================================================================
"""

from __future__ import annotations

import asyncio
import sys

from pawabase_server import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "serve"

    if command == "version":
        print(__version__)
        return 0

    from pawabase_server.settings import Settings

    if command == "check":
        settings = Settings()
        problems = settings.validate_for_production() if settings.app_env == "production" else []
        for problem in problems:
            print(problem, file=sys.stderr)
        print("configuration ok" if not problems else "configuration has problems")
        return 1 if problems else 0

    if command == "migrate":
        from pawabase_server.db.database import main as migrate

        asyncio.run(migrate(args))
        return 0

    if command == "copy-data":
        from pawabase_server.data.copy_cli import run as copy_data

        return asyncio.run(copy_data(args, Settings()))

    if command in ("worker", "scheduler"):
        import logging

        from pawabase_server.automation.runner import run

        settings = Settings()
        logging.basicConfig(
            level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
        )
        try:
            asyncio.run(run(command, settings))
        except KeyboardInterrupt:
            pass
        return 0

    if command == "serve":
        import logging

        from pawabase_server.server import serve

        settings = Settings()
        logging.basicConfig(
            level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
        )
        try:
            asyncio.run(serve(settings))
        except KeyboardInterrupt:
            pass
        return 0

    print(
        f"usage: pawabase-server [serve|worker|scheduler|migrate|copy-data|check|version] (got {command!r})",
        file=sys.stderr,
    )
    return 64
