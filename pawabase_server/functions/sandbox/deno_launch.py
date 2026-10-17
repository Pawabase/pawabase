"""Start a TypeScript worker: confine this process, then become Deno.

``python -m pawabase_server.functions.sandbox.deno_launch <serve|describe> <artifact> <tmp> <memory_mb> <confinement>``

The limits and the Landlock/seccomp confinement a Python worker applies to itself are applied here, before ``exec``; the kernel keeps them for
the program that replaces this one. When confinement is required and cannot be applied, nothing of the deployment runs: the launcher answers
the host's ``init`` (or the describe call) with the refusal and exits.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pawabase_server.functions.sandbox.confine import ConfinementUnavailable, confine
from pawabase_server.functions.sandbox.typescript import KIT, deno_command, deno_path
from pawabase_server.functions.sandbox.worker import apply_limits


def main() -> None:
    mode, artifact, tmp, memory, confinement = sys.argv[1:6]
    artifact_path, tmp_path = Path(artifact), Path(tmp)
    apply_limits(
        {"files": 256, "file_mb": 64}
    )  # no address-space limit: V8 gets a heap limit instead
    command = deno_command(mode, artifact_path, tmp_path, int(memory))
    binary = Path(deno_path()).resolve()
    state = "off"
    if confinement != "off":
        try:
            confine(confinement, artifact=artifact_path, libs=[KIT, binary.parent], tmp=tmp_path)
            state = confinement
        except ConfinementUnavailable as exc:
            reason = str(exc)
            if mode == "serve":
                sys.stdin.readline()  # the host's init
                print(
                    json.dumps({"t": "ready", "functions": [], "errors": [], "unconfined": reason}),
                    flush=True,
                )
            else:
                print(json.dumps({"specs": [], "errors": [], "unconfined": reason}), flush=True)
            sys.exit(0)
    os.environ.update(
        {
            "PAWABASE_CONFINED": state,
            "DENO_DIR": str(tmp_path / "deno"),
            "DENO_NO_UPDATE_CHECK": "1",
            "NO_COLOR": "1",
            "HOME": str(tmp_path),
        }
    )
    os.execv(str(binary), command)


if __name__ == "__main__":
    main()
