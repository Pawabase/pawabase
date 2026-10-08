"""``python -m pawabase_core.supervisor``: every Pawabase process in one container.

A host that deploys one image per service cannot share the image Pawabase builds, so the hosted setup runs the API, worker, scheduler, Akountz, Angula,
gateway and Studio as child processes of this one. They talk over loopback. If any child exits the others are stopped and this process exits with its
status, so the host's restart policy restarts the whole installation together.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

PYTHON = sys.executable
UVICORN = [PYTHON, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--proxy-headers", "--forwarded-allow-ips", "*"]
#: (name, working directory, command, port): started in this order, the API first and alone.
SERVICES = [
    ("api", "/app/api", [*UVICORN, "--port", "8001"], 8001),
    ("worker", "/app/api", [PYTHON, "-m", "app.worker"], None),
    ("scheduler", "/app/api", [PYTHON, "-m", "app.scheduler"], None),
    ("akountz", "/app/akountz", [*UVICORN, "--port", "8002"], 8002),
    ("angula", "/app/angula", [*UVICORN, "--port", "8003"], 8003),
    ("gateway", "/app/gateway", [*UVICORN, "--port", "8080"], 8080),
    ("studio", "/app/studio", [*UVICORN, "--port", "8090"], 8090),
]


def database_url_for(base: str, database: str) -> str:
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path=f"/{database}"))


def environment_for(name: str, base: dict[str, str]) -> dict[str, str]:
    """The environment of one child: the shared one, with services found on loopback and Akountz on its own database."""
    env = dict(base)
    env.setdefault("PAWABASE_API_URL", "http://127.0.0.1:8001")
    env.setdefault("PAWABASE_AKOUNTZ_URL", "http://127.0.0.1:8002")
    env.setdefault("PAWABASE_ANGULA_URL", "http://127.0.0.1:8003")
    env.setdefault("PAWABASE_GATEWAY_URL", "http://127.0.0.1:8080")
    env.setdefault("PAWABASE_STUDIO_URL", "http://127.0.0.1:8090")
    url = base.get("PAWABASE_DATABASE_URL", "")
    if name == "akountz" and url.startswith(("postgres://", "postgresql://")):
        env["PAWABASE_DATABASE_URL"] = base.get("PAWABASE_AKOUNTZ_DATABASE_URL") or database_url_for(url, "akountz")
    return env


async def wait_healthy(port: int, process: asyncio.subprocess.Process, timeout: float = 120) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline and process.returncode is None:
        try:
            await asyncio.to_thread(urllib.request.urlopen, f"http://127.0.0.1:{port}/health", None, 3)
            return True
        except (urllib.error.URLError, OSError):
            await asyncio.sleep(1)
    return False


async def main() -> int:
    base = dict(os.environ)
    processes: dict[str, asyncio.subprocess.Process] = {}
    stopping = asyncio.Event()

    def stop(*_: object) -> None:
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop)

    async def start(name: str, cwd: str, command: list[str]) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(*command, cwd=cwd, env=environment_for(name, base))
        processes[name] = process
        return process

    for name, cwd, command, _port in SERVICES:
        process = await start(name, cwd, command)
        if name == "api" and not await wait_healthy(8001, process):
            print("api did not become healthy", file=sys.stderr)
            stopping.set()
            break

    waiters = {asyncio.create_task(p.wait()): n for n, p in processes.items()}
    stopper = asyncio.create_task(stopping.wait())
    await asyncio.wait([*waiters, stopper], return_when=asyncio.FIRST_COMPLETED)
    status = 0
    for task, name in waiters.items():
        if task.done() and not stopping.is_set():
            status = task.result() or 1
            print(f"{name} exited with {task.result()}: stopping the installation", file=sys.stderr)
    for process in processes.values():
        if process.returncode is None:
            process.terminate()
    await asyncio.gather(*(p.wait() for p in processes.values()))
    return status


def akountz_url() -> str:
    return environment_for("akountz", dict(os.environ)).get("PAWABASE_DATABASE_URL", "")


if __name__ == "__main__":
    if "--akountz-url" in sys.argv:  # for the entrypoint: the database Akountz migrates
        print(akountz_url())
        raise SystemExit(0)
    raise SystemExit(asyncio.run(main()))
