"""Python the operator mounts next to the server: ``policies.py``, ``transformers.py`` and ``routes.py`` of the code directory.

This is the platform's own, trusted code (it runs in the server process, like any other module of the installation), so it is loaded by a plain
import. A module that fails to import is reported and skipped: one broken file must not stop the platform from serving.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawabase.code")

PREFIX = "pawabase_code"
FILES = ("policies.py", "transformers.py", "routes.py")


@dataclass(slots=True)
class MountedCode:
    """What loading the code directory produced."""

    modules: list[str] = field(default_factory=list)
    router: Any = None
    errors: list[str] = field(default_factory=list)


def _import_file(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _on_path(root: Path):
    """Let the mounted files import helper modules that sit next to them."""
    saved = list(sys.path)
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path[:] = saved


def load_mounted_code(base: str | Path) -> MountedCode:
    """Import the mounted files that exist in *base*."""
    result = MountedCode()
    base = Path(base)
    if not base.is_dir():
        return result
    with _on_path(base):
        for filename in FILES:
            path = base / filename
            if not path.is_file():
                continue
            name = f"{PREFIX}.{path.stem}"
            try:
                module = _import_file(name, path)
                result.modules.append(name)
                if filename == "routes.py":
                    result.router = getattr(module, "router", None)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                logger.exception("could not load %s", path)
                result.errors.append(f"{filename}: {type(exc).__name__}: {exc}")
    return result


def forget_mounted_code() -> None:
    """Drop the imported mounted modules so the next load reads the files again."""
    for name in [n for n in sys.modules if n.startswith(f"{PREFIX}.")]:
        del sys.modules[name]
