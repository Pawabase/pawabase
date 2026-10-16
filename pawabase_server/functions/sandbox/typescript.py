"""TypeScript functions: run by Deno, in the same worker protocol and the same confinement as Python ones.

A deployment is one language: Python (``functions/*.py``) or TypeScript (``functions/*.ts``). A TypeScript worker is started through
:mod:`pawabase_server.functions.sandbox.deno_launch`, which applies the sandbox's limits and Landlock/seccomp confinement to itself and then
becomes Deno (both survive ``exec``). Deno adds its own layer: no network, no environment, no subprocesses, and reading only the deployment
and the kit. Functions import the kit as ``"pawabase"`` (``deno/pawabase.ts``).

Imports are local files only (``--no-remote``): a deployment carries its own code; ``npm:`` and URL imports are not fetched on the server.
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

PYTHON = "python"
TYPESCRIPT = "typescript"
SOURCES = (".ts", ".mts", ".js", ".mjs")
KIT = Path(__file__).resolve().parent / "deno"
DESCRIBE_SECONDS = 30


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
    """The language of a bundle from its file names: ``python``, ``typescript``, ``mixed`` or ``None`` (no functions)."""
    functions = [
        n for n in names if n.startswith("functions/") and "/" not in n[len("functions/") :]
    ]
    python = any(n.endswith(".py") for n in functions)
    typescript = any(n.endswith(SOURCES) and not n.endswith(".d.ts") for n in functions)
    if python and typescript:
        return "mixed"
    return PYTHON if python else TYPESCRIPT if typescript else None


def deno_command(mode: str, artifact: Path, tmp: Path, memory_mb: int) -> list[str]:
    """The Deno command line for *mode* (``serve`` or ``describe``)."""
    import_map = tmp / "import_map.json"
    import_map.write_text(json.dumps({"imports": {"pawabase": (KIT / "pawabase.ts").as_uri()}}))
    command = [
        deno_path(),
        "run",
        "--quiet",
        "--no-prompt",
        "--no-remote",
        "--no-lock",
        "--no-config",
        f"--allow-read={artifact},{KIT},{tmp}",
        f"--allow-write={tmp}",
        "--allow-env=PAWABASE_CONFINED",
        f"--import-map={import_map}",
    ]
    if memory_mb:
        # V8 reserves far more address space than it uses, so its heap limit replaces the address-space limit Python workers get.
        command.append(f"--v8-flags=--max-old-space-size={memory_mb}")
    command.append(str(KIT / "worker.ts"))
    if mode == "describe":
        command += ["--describe", str(artifact)]
    return command


def launcher(
    mode: str, artifact: Path, tmp: Path, *, memory_mb: int, confinement: str
) -> list[str]:
    """The command that starts a confined Deno: the launcher confines itself, then becomes Deno."""
    return [
        sys.executable,
        "-m",
        "pawabase_server.functions.sandbox.deno_launch",
        mode,
        str(artifact),
        str(tmp),
        str(memory_mb),
        confinement,
    ]


def describe(artifact: Path, *, environment: dict[str, str], confinement: str) -> dict[str, Any]:
    """Load a TypeScript deployment once, confined, and return ``{"specs": [...], "errors": [...]}``."""
    tmp = Path(tempfile.mkdtemp(prefix="pawabase-describe-"))
    try:
        done = subprocess.run(
            launcher("describe", artifact, tmp, memory_mb=512, confinement=confinement),
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
                "errors": [
                    f"The TypeScript code could not be loaded: {' '.join(tail) or f'exit {done.returncode}'}"
                ],
            }
        answer = json.loads(lines[-1])
        if answer.get("unconfined"):
            return {
                "specs": [],
                "errors": [f"The function process could not be confined: {answer['unconfined']}"],
            }
        return answer
    except subprocess.TimeoutExpired:
        return {
            "specs": [],
            "errors": [f"Loading the TypeScript code took longer than {DESCRIBE_SECONDS}s."],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
