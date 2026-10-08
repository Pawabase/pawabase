"""What a deployment is using, for whoever hosts it.

A host that runs many deployments needs to know, per deployment, how much memory the process
holds, how big each environment's database has grown and how many bytes its objects take, to
bill by usage, enforce a plan or decide where the next deployment fits. This reports those, on
demand: nothing here runs in the background.
"""

from __future__ import annotations

import os
import resource
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from database.models import ApiKey, Bucket, Environment

if TYPE_CHECKING:
    from app.platform import Platform

STARTED = time.time()
#: Most files counted per environment's local storage before saying "at least".
FILE_LIMIT = 50_000


def process_memory_bytes() -> int | None:
    """The resident memory of this process: ``VmRSS`` on Linux, the peak elsewhere."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ValueError, OSError):
        return None
    return peak if sys.platform == "darwin" else peak * 1024


def directory_bytes(path: Path, limit: int = FILE_LIMIT) -> tuple[int, bool]:
    """Bytes under *path*, and whether the count stopped at *limit* files."""
    total = files = 0
    for current, _dirs, names in os.walk(path):
        for name in names:
            files += 1
            if files > limit:
                return total, True
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total, False


async def environment_usage(platform: Platform, environment: Environment) -> dict[str, Any]:
    state = await platform.state(environment.name)
    source = await state.source()
    storage = platform.storage._config(state)
    usage: dict[str, Any] = {
        "name": environment.name,
        "api_keys": await ApiKey.filter(environment=environment, revoked_at=None).count(),
        "buckets": await Bucket.filter(environment=environment).count(),
        "resources": len(state.resources),
        "database": {"dialect": source.dialect, "bytes": await source.size_bytes()},
        "storage": {"driver": storage.get("driver"), "bytes": None, "truncated": False},
    }
    if storage.get("driver") == "local":
        root = Path(str(storage.get("root") or platform.settings.storage_root)) / environment.name
        size, truncated = directory_bytes(root) if root.is_dir() else (0, False)
        usage["storage"].update(bytes=size, truncated=truncated)
    return usage


async def report(platform: Platform) -> dict[str, Any]:
    """The deployment's footprint: the process, then each environment."""
    environments = []
    for environment in await Environment.all().order_by("name"):
        try:
            environments.append(await environment_usage(platform, environment))
        except Exception as exc:  # one environment's database being down must not hide the others
            environments.append({"name": environment.name, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "process": {
            "memory_bytes": process_memory_bytes(),
            "uptime_seconds": int(time.time() - STARTED),
            "pid": os.getpid(),
        },
        "limits": {
            "max_environments": platform.settings.max_environments,
            "max_upload_bytes": platform.settings.max_upload_bytes,
        },
        "environments": environments,
    }
