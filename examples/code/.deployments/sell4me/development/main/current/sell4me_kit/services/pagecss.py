"""CSS for the Tailwind classes merchants put on blocks.

The builder's Style tab writes classes onto a block — `rounded-3xl`,
`bg-[radial-gradient(...)]`, `md:text-7xl`. Those live in the database, and the
shop's stylesheet is compiled from source files, so it has never seen them: a
class that looked right on the canvas (whose iframe runs a Tailwind of its own)
did nothing at all on the published page.

So a page's classes are compiled on the server, once per distinct set, by
`assets/page-css.mjs` using the same Tailwind the build uses, and the result
travels with the page. Cached on disk by a hash of the class list: a page that
has not changed costs one file read.

Safe to feed merchant input because every class has been through
`builder._safe_classes` — no quotes, semicolons or braces — and Tailwind emits
declarations only for class names it can parse.

When Node is unavailable the page renders without the extra CSS rather than
failing; the blocks' own styling does not depend on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

#: Where ``node_modules`` (``tailwindcss`` and ``@tailwindcss/node``) is installed: ``SELL4ME_NODE_DIR``, else the project folder this kit sits in when run from a
#: checkout. A deployed copy has no ``node_modules`` beside it, so set the variable on the server that runs the functions; without Node pages render without it.
NODE_DIR = Path(os.environ.get("SELL4ME_NODE_DIR") or Path(__file__).resolve().parents[3])

__all__ = ["classes_in", "css_for"]

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "page-css.mjs"
CACHE = Path(tempfile.gettempdir()) / "sell4me-page-css"

#: Longest a compile may take before the page renders without it.
TIMEOUT_SECONDS = 20


def classes_in(tree: Any) -> list[str]:
    """Every class used anywhere in a tree, sorted and without repeats."""
    found: set[str] = set()

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            props = node.get("props")
            if isinstance(props, dict) and isinstance(props.get("styles"), str):
                found.update(props["styles"].split())
            walk(node.get("children"))

    walk(tree)
    return sorted(found)


async def css_for(tree: Any) -> str:
    """The stylesheet for a tree's classes. Empty when it has none."""
    classes = classes_in(tree)
    if not classes:
        return ""

    digest = hashlib.sha256("\n".join(classes).encode()).hexdigest()[:24]
    cached = CACHE / f"{digest}.css"
    if cached.exists():
        return cached.read_text()

    css = await _compile(classes)
    if css:
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(css)
    return css


async def _compile(classes: list[str]) -> str:
    node = shutil.which("node")
    if node is None or not SCRIPT.exists() or not (NODE_DIR / "node_modules").is_dir():
        return ""
    try:
        process = await asyncio.create_subprocess_exec(
            node,
            "--input-type=module",
            "-e",
            SCRIPT.read_text(),
            cwd=str(NODE_DIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(classes).encode()), TIMEOUT_SECONDS
        )
    except (OSError, TimeoutError) as error:
        log.warning("page css: compile failed: %s", error)
        return ""
    if process.returncode != 0:
        log.warning("page css: compile exited %s: %s", process.returncode, stderr[-500:])
        return ""
    return stdout.decode()

