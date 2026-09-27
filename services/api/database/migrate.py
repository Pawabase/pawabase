"""Apply pending migrations: ``python -m database.migrate`` (or ``make NAME``).

Containers run this before the service starts. ``make`` writes a new
migration from the current models, for contributors changing them.
"""

from __future__ import annotations

import asyncio
import sys

from sillo.record.commands import make, migrate

from app.config import ApiSettings
from database.config import database


async def main(argv: list[str]) -> None:
    manager = database(ApiSettings())
    async with manager:
        if argv[:1] == ["make"]:
            await make(manager, argv[1] if len(argv) > 1 else "auto")
        else:
            await migrate(manager)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
