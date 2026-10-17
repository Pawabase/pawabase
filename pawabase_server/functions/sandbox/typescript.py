"""TypeScript functions: run by Deno, in the same worker protocol and the same confinement as Python ones.

A deployment is one language: Python (``functions/*.py``) or TypeScript (``functions/*.ts`` for ``fn()`` functions, and
``functions/<name>/index.ts`` for HTTP functions). A TypeScript worker is started through
:mod:`pawabase_server.functions.sandbox.deno_launch`, which applies the sandbox's limits and Landlock/seccomp confinement to itself and then
becomes Deno (both survive ``exec``). Deno adds its own layer: no network, no subprocesses, and reading only the deployment, the kit and
the deployment's vendored modules. Functions import the kit as ``"@pawabase/functions"`` or ``"pawabase"`` (``deno/pawabase.ts``).

Imports are resolved from the deployment's import maps (:mod:`pawabase_server.functions.vendor`). ``npm:``, ``jsr:`` and URL imports are
fetched once, when the deployment is activated; a worker runs ``--cached-only`` from that copy and never fetches anything itself.

Three programs run in Deno:

``describe``   load the deployment once and print what it defines (``fn()`` specs and HTTP functions with their ``config``).
``serve``      the ``fn()`` worker: one call at a time, over the JSON-lines protocol of ``worker.py``.
``http``       one HTTP function's worker: many requests at once, streamed (``deno/http_worker.ts``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pawabase_server.functions import vendor

PYTHON = "python"
TYPESCRIPT = "typescript"
SOURCES = (".ts", ".mts", ".js", ".mjs")
ENTRIES = tuple(f"index{suffix}" for suffix in SOURCES)
#: What a function imports the kit as: the npm package name, and the short name the server always accepted.
KIT_SPECIFIERS = vendor.KIT_SPECIFIERS
KIT = Path(__file__).resolve().parent / "deno"
DESCRIBE_SECONDS = 60
PROGRAMS = {"serve": "worker.ts", "describe": "worker.ts", "http": "http_worker.ts"}


class RuntimeUnavailable(RuntimeError):
    """Deno is not installed where the platform runs."""


def deno_path() -> str:
    found = os.environ.get("PAWABASE_DENO_PATH") or shutil.which("deno")
    if not found:
        raise RuntimeUnavailable(
            "TypeScript functions need Deno, which is not installed on this server (set PAWABASE_DENO_PATH or install deno)."
        )
    return found


def language_of(names: Sequence[str]) -> str | None:
    """The language of a bundle from its file names: ``python``, ``typescript``, ``mixed`` or ``None`` (no functions).

    ``functions/*.py`` is Python; ``functions/*.ts`` and an HTTP function's ``functions/<name>/index.ts`` are TypeScript.
    """
    top = [n for n in names if n.startswith("functions/") and "/" not in n[len("functions/") :]]
    python = any(n.endswith(".py") for n in top)
    typescript = any(n.endswith(SOURCES) and not n.endswith(".d.ts") for n in top) or any(
        is_http_entry(n) for n in names
    )
    if python and typescript:
        return "mixed"
    return PYTHON if python else TYPESCRIPT if typescript else None


def is_http_entry(name: str) -> bool:
    """Whether a bundle path is an HTTP function's entry file: ``functions/<name>/index.ts`` (not in a ``_shared``-style folder)."""
    parts = name.split("/")
    return (
        len(parts) == 3
        and parts[0] == "functions"
        and not parts[1].startswith(("_", "."))
        and parts[2] in ENTRIES
    )


def deno_command(
    mode: str,
    artifact: Path,
    tmp: Path,
    memory_mb: int,
    *,
    function: str | None = None,
    entry_cache: Path | None = None,
    network: str = "guarded",
) -> list[str]:
    """The Deno command line for *mode* (``serve``, ``describe`` or ``http``).

    ``entry_cache`` is the deployment's vendored module cache (``None`` when it imports nothing remote); ``function`` names the HTTP
    function whose own import map applies.
    """
    mapping = vendor.import_map(artifact, KIT, function if mode == "http" else None)
    import_map = tmp / "import_map.json"
    import_map.write_text(json.dumps(mapping))
    readable = [str(artifact), str(KIT), str(tmp)]
    if entry_cache is not None:
        readable.append(str(entry_cache.resolve()))
    command = [
        deno_path(),
        "run",
        "--quiet",
        "--no-prompt",
        "--no-lock",
        "--no-config",
        "--node-modules-dir=none",
        "--cached-only" if entry_cache is not None else "--no-remote",
        f"--allow-read={','.join(readable)}",
        f"--allow-write={tmp}",
        f"--import-map={import_map}",
    ]
    if mode == "http":
        # The environment is curated by the runtime (the function's secrets and a few built-ins), so all of it may be read.
        command.append("--allow-env")
        if network == "direct":
            command.append("--allow-net")
    else:
        command.append("--allow-env=PAWABASE_CONFINED")
    if memory_mb:
        # V8 reserves far more address space than it uses, so its heap limit replaces the address-space limit Python workers get.
        command.append(f"--v8-flags=--max-old-space-size={memory_mb}")
    command.append(str(KIT / PROGRAMS[mode]))
    if mode == "describe":
        command += ["--describe", str(artifact)]
    return command


def launcher(
    mode: str,
    artifact: Path,
    tmp: Path,
    *,
    memory_mb: int,
    confinement: str,
    function: str | None = None,
    cache: Path | None = None,
    network: str = "guarded",
) -> list[str]:
    """The command that starts a confined Deno: the launcher confines itself, then becomes Deno."""
    options = {
        "mode": mode,
        "artifact": str(artifact),
        "tmp": str(tmp),
        "memory_mb": memory_mb,
        "confinement": confinement,
        "function": function,
        "cache": str(cache) if cache is not None else None,
        "network": network,
    }
    return [sys.executable, "-m", "pawabase_server.functions.sandbox.deno_launch", json.dumps(options)]


def describe(
    artifact: Path,
    *,
    environment: dict[str, str],
    confinement: str,
    cache: Path | None = None,
) -> dict[str, Any]:
    """Load a TypeScript deployment once, confined, and return ``{"specs": [...], "http": [...], "errors": [...]}``."""
    tmp = Path(tempfile.mkdtemp(prefix="pawabase-describe-"))
    try:
        done = subprocess.run(
            launcher("describe", artifact, tmp, memory_mb=512, confinement=confinement, cache=cache),
            capture_output=True,
            timeout=DESCRIBE_SECONDS,
            env={**environment, "TMPDIR": str(tmp), "HOME": str(tmp)},
            cwd=str(Path(__file__).resolve().parents[3]),
            check=False,
        )
        lines = [line for line in done.stdout.decode(errors="replace").splitlines() if line.strip()]
        if not lines:
            tail = done.stderr.decode(errors="replace").strip().splitlines()[-3:]
            return {
                "specs": [],
                "http": [],
                "errors": [
                    f"The TypeScript code could not be loaded: {' '.join(tail) or f'exit {done.returncode}'}"
                ],
            }
        answer = json.loads(lines[-1])
        if answer.get("unconfined"):
            return {
                "specs": [],
                "http": [],
                "errors": [f"The function process could not be confined: {answer['unconfined']}"],
            }
        answer.setdefault("http", [])
        return answer
    except subprocess.TimeoutExpired:
        return {
            "specs": [],
            "http": [],
            "errors": [f"Loading the TypeScript code took longer than {DESCRIBE_SECONDS}s."],
        }
    except vendor.VendorError as exc:
        return {"specs": [], "http": [], "errors": [exc.message, *exc.problems]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
