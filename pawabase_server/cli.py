"""The ``pawabase-server`` command.

=========  ==============================================================================
serve      run the server (the default)
migrate    apply pending database migrations; ``migrate make NAME`` writes a new one
check      validate the configuration and exit non-zero when something is wrong
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
        f"usage: pawabase-server [serve|migrate|check|version] (got {command!r})", file=sys.stderr
    )
    return 64
