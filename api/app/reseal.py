"""``python -m app.reseal``: seal existing secrets for their own environment.

Secrets sealed before environments had keys of their own open under the master key and keep
working; nothing requires rewriting them. This does it anyway, for a deployment that wants every
secret bound to its environment: stored secrets, webhook signing secrets and inbound-hook secrets.
It is safe to run twice (a value already bound is left alone) and ``--dry-run`` only counts.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

#: model name, ciphertext field
TARGETS = (
    ("Secret", "ciphertext"),
    ("WebhookEndpoint", "secret_ciphertext"),
    ("InboundHook", "secret_ciphertext"),
)


async def reseal(box: Any, *, dry_run: bool = False) -> dict[str, dict[str, int]]:
    """Seal every legacy ciphertext for its row's environment. Returns counts per model."""
    from database import models

    report: dict[str, dict[str, int]] = {}
    for name, field in TARGETS:
        model = getattr(models, name)
        counts = {"sealed": 0, "already_bound": 0, "unreadable": 0, "empty": 0}
        for row in await model.all().prefetch_related("environment"):
            value = getattr(row, field)
            if not value:
                counts["empty"] += 1
            elif box.is_bound(value):
                counts["already_bound"] += 1
            else:
                try:
                    sealed = box.seal(box.open(value), row.environment.name)
                except Exception:  # sealed under another master key: leave it as it is
                    counts["unreadable"] += 1
                    continue
                counts["sealed"] += 1
                if not dry_run:
                    setattr(row, field, sealed)
                    await row.save(update_fields=[field])
        report[name] = counts
    return report


async def run(dry_run: bool) -> dict[str, dict[str, int]]:
    from sillo.record import DatabaseManager

    from app.config import ApiSettings
    from database.config import MODEL_MODULES, database_config
    from pawabase_core.crypto import SecretBox

    settings = ApiSettings()
    database = DatabaseManager(database_config(settings)).register_models(*MODEL_MODULES)
    await database.init()
    try:
        return await reseal(SecretBox(settings.master_key), dry_run=dry_run)
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.reseal", description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="count what would change, change nothing"
    )
    arguments = parser.parse_args(argv)
    for name, counts in asyncio.run(run(arguments.dry_run)).items():
        print(f"{name}: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
