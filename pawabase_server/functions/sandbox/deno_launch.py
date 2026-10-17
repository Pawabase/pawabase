"""Start a TypeScript worker: confine this process, then become Deno.

``python -m pawabase_server.functions.sandbox.deno_launch '<json options>'`` with ``mode`` (``serve``, ``describe`` or ``http``),
``artifact``, ``tmp``, ``memory_mb``, ``confinement``, and for HTTP functions ``function``, ``cache`` (the vendored module cache) and
``network`` (``guarded`` or ``direct``).

The limits and the Landlock/seccomp confinement a Python worker applies to itself are applied here, before ``exec``; the kernel keeps them for
the program that replaces this one. When confinement is required and cannot be applied, nothing of the deployment runs: the launcher answers
the host's ``init`` (or the describe call) with the refusal and exits.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pawabase_server.functions import vendor
from pawabase_server.functions.sandbox.confine import ConfinementUnavailable, confine
from pawabase_server.functions.sandbox.typescript import KIT, deno_command, deno_path
from pawabase_server.functions.sandbox.worker import apply_limits


def _refuse(mode: str, reason: str) -> None:
    if mode in ("serve", "http"):
        sys.stdin.readline()  # the host's init
        message = {"t": "ready", "functions": [], "errors": [], "unconfined": reason}
        print(json.dumps(message), flush=True)
    else:
        print(json.dumps({"specs": [], "http": [], "errors": [], "unconfined": reason}), flush=True)
    sys.exit(0)


def main() -> None:
    options = json.loads(sys.argv[1])
    mode = options["mode"]
    artifact_path, tmp_path = Path(options["artifact"]), Path(options["tmp"])
    cache = Path(options["cache"]) if options.get("cache") else None
    network = options.get("network") or "guarded"
    apply_limits(
        {"files": 1024 if mode == "http" else 256, "file_mb": 64}
    )  # no address-space limit: V8 gets a heap limit instead
    deno_dir = tmp_path / "deno"
    if cache is not None:
        # The worker's own DENO_DIR, with the vendored modules linked in: Deno writes its databases here, reads modules there.
        deno_dir.mkdir(parents=True, exist_ok=True)
        for name in vendor.SHARED_CACHE_ENTRIES:
            source, link = cache / name, deno_dir / name
            if source.exists() and not link.exists():
                link.symlink_to(source, target_is_directory=True)
    try:
        command = deno_command(
            mode,
            artifact_path,
            tmp_path,
            int(options.get("memory_mb") or 0),
            function=options.get("function"),
            entry_cache=cache,
            network=network,
        )
    except vendor.VendorError as exc:
        _refuse(mode, f"{exc.message} {' '.join(exc.problems)}".strip())
        return
    binary = Path(deno_path()).resolve()
    state = "off"
    confinement = options.get("confinement") or "off"
    if confinement != "off":
        libraries = [KIT, binary.parent, *([cache.resolve()] if cache is not None else [])]
        try:
            confine(
                confinement,
                artifact=artifact_path,
                libs=libraries,
                tmp=tmp_path,
                network=network == "direct" and mode == "http",
            )
            state = confinement
        except ConfinementUnavailable as exc:
            _refuse(mode, str(exc))
            return
    os.environ.update(
        {
            "PAWABASE_CONFINED": state,
            "DENO_DIR": str(deno_dir),
            "DENO_NO_UPDATE_CHECK": "1",
            "NO_COLOR": "1",
            "HOME": str(tmp_path),
        }
    )
    os.execv(str(binary), command)


if __name__ == "__main__":
    main()
