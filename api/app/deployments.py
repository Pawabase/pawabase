"""Function artifacts on disk: what ``pawabase deploy`` uploaded, per project, environment and branch.

::

    <deployments_path>/<project>/<env>/<branch>/
        current/                       the active artifact, unpacked (functions/*.py and the helper packages shipped with it)
        current/.pawabase-deployment   {"id", "checksum"} of the active deployment: the one thing every process reads
        archives/<deployment id>.tar.gz   every artifact ever deployed, so a rollback re-activates bytes that really ran before

The platform may run as several processes (API workers, job workers). A deploy lands in one of them, so every process looks at the stamp file before it
resolves a function, at most once a second, and reloads when the id changed: nothing has to be told about a deployment, and a process that starts later
loads what is current without a database read.

The unit that is switched is the whole ``current/`` folder: an upload is unpacked beside it, compiled, imported, and only then swapped in; if the new code
does not import, the previous artifact is put back and nothing was ever served from the broken one.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pawabase.functions import module_prefix
from pawabase_core.functions import (
    MAIN,
    ProjectCode,
    clear_functions,
    deployment_key,
    load_code_dir,
)

logger = logging.getLogger("pawabase.deployments")

STAMP = ".pawabase-deployment"
MAX_FILES = 2_000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
RECHECK_SECONDS = 1.0
#: Loaded for a whole project from the mounted code directory, never from an upload: they change what the platform enforces and routes, not what a function does.
PROJECT_LEVEL = ("routes.py", "policies.py", "transformers.py")


class DeploymentError(Exception):
    """An artifact that cannot be activated. ``problems`` says why, one line each."""

    def __init__(self, message: str, problems: list[str] | None = None, *, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.problems, self.status = message, problems or [], status


@dataclass
class _Seen:
    checked_at: float = 0.0
    loaded_id: str | None = None
    code: ProjectCode | None = None
    modules: list[str] = field(default_factory=list)


def unpack(archive: bytes, destination: Path) -> int:
    """Extract a source bundle, trusting nothing about its paths, links, sizes or file count. Returns the number of files."""
    files = total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) > MAX_FILES:
                raise DeploymentError("The bundle contains too many files.")
            for member in members:
                path = PurePosixPath(member.name)
                if member.issym() or member.islnk() or member.isdev() or path.is_absolute() or ".." in path.parts:
                    raise DeploymentError(f"The bundle contains an unsafe entry: {member.name}")
                if not member.isfile():
                    continue
                if member.size > MAX_FILE_BYTES:
                    raise DeploymentError(f"{member.name} is larger than {MAX_FILE_BYTES // 1024 // 1024}MB.")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise DeploymentError("The bundle is too large when unpacked.")
                source = bundle.extractfile(member)
                if source is None:
                    continue
                target = destination.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                files += 1
    except tarfile.TarError as exc:
        raise DeploymentError("The bundle must be a gzip tar archive.") from exc
    return files


class Deployments:
    """Install, find, load and remove function artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._seen: dict[str, _Seen] = {}

    # ── where things are ────────────────────────────────────────────────

    def folder(self, project: str, env: str, branch: str = MAIN) -> Path:
        return self.root / project / env / (branch or MAIN)

    def current(self, project: str, env: str, branch: str = MAIN) -> Path:
        return self.folder(project, env, branch) / "current"

    def archive(self, project: str, env: str, branch: str, deployment_id: str) -> Path:
        return self.folder(project, env, branch) / "archives" / f"{deployment_id}.tar.gz"

    def stamp(self, project: str, env: str, branch: str = MAIN) -> dict[str, Any] | None:
        try:
            return json.loads((self.current(project, env, branch) / STAMP).read_text())
        except (OSError, ValueError):
            return None

    def branches(self, project: str, env: str) -> list[str]:
        base = self.root / project / env
        if not base.is_dir():
            return []
        return sorted(entry.name for entry in base.iterdir() if (entry / "current").is_dir())

    # ── loading ─────────────────────────────────────────────────────────

    def ensure_loaded(self, project: str, env: str, branch: str = MAIN) -> ProjectCode | None:
        """Make this process serve what is currently deployed. Cheap: one small file read, at most once a second per deployment."""
        key = deployment_key(project, env, branch)
        seen = self._seen.setdefault(key, _Seen())
        now = time.monotonic()
        if now - seen.checked_at < RECHECK_SECONDS:
            return seen.code
        seen.checked_at = now
        stamp = self.stamp(project, env, branch)
        wanted = stamp["id"] if stamp else None
        if wanted == seen.loaded_id:
            return seen.code
        if wanted is None:
            clear_functions(key)
            seen.loaded_id, seen.code = None, None
            return None
        return self.load(project, env, branch)

    def load(self, project: str, env: str, branch: str = MAIN) -> ProjectCode:
        key = deployment_key(project, env, branch)
        seen = self._seen.setdefault(key, _Seen())
        clear_functions(key)
        for name in [name for name in sys.modules if name.startswith(f"{module_prefix(key)}.")]:
            del sys.modules[name]
        code = load_code_dir(self.current(project, env, branch), key, only_functions=True, purge_under=self.root)
        stamp = self.stamp(project, env, branch)
        seen.checked_at, seen.loaded_id, seen.code = time.monotonic(), (stamp or {}).get("id"), code
        if code.errors:
            logger.warning("deployment %s has errors: %s", key, code.errors)
        return code

    # ── installing ──────────────────────────────────────────────────────

    def install(self, project: str, env: str, branch: str, archive: bytes, *, deployment_id: str, checksum: str, keep_archive: bool = True) -> ProjectCode:
        """Activate *archive*, or raise :class:`DeploymentError` having changed nothing that is served."""
        folder = self.folder(project, env, branch)
        folder.mkdir(parents=True, exist_ok=True)
        staging = folder / f".staging-{deployment_id}"
        previous = folder / f".previous-{deployment_id}"
        current = folder / "current"
        try:
            files = unpack(archive, staging)
            problems = self._check(staging)
            if problems:
                raise DeploymentError("The bundle was refused.", problems)
            (staging / STAMP).write_text(json.dumps({"id": deployment_id, "checksum": checksum, "files": files, "activated_at": time.time()}))
            if current.exists():
                current.rename(previous)
            staging.rename(current)
            code = self.load(project, env, branch)
            if code.errors:
                raise DeploymentError("The new code does not load, so it was not activated.", code.errors)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if previous.exists():
                if current.exists():
                    shutil.rmtree(current, ignore_errors=True)
                previous.rename(current)
                self.load(project, env, branch)
            elif current.exists() and not self.stamp(project, env, branch):
                shutil.rmtree(current, ignore_errors=True)
            raise
        finally:
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
        if keep_archive:
            target = self.archive(project, env, branch, deployment_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive)
        return code

    def reactivate(self, project: str, env: str, branch: str, deployment_id: str) -> ProjectCode:
        """Put an earlier artifact back (a rollback). Raises :class:`DeploymentError` when its bytes are gone."""
        source = self.archive(project, env, branch, deployment_id)
        if not source.is_file():
            raise DeploymentError("That deployment's artifact is no longer stored.", status=409)
        data = source.read_bytes()
        return self.install(project, env, branch, data, deployment_id=deployment_id, checksum=hashlib.sha256(data).hexdigest(), keep_archive=False)

    def remove_branch(self, project: str, env: str, branch: str) -> bool:
        if branch == MAIN:
            raise DeploymentError("The main deployment is replaced by deploying again, not removed.", status=409)
        folder = self.folder(project, env, branch)
        if not folder.exists():
            return False
        clear_functions(deployment_key(project, env, branch))
        self._seen.pop(deployment_key(project, env, branch), None)
        shutil.rmtree(folder)
        return True

    @staticmethod
    def _check(directory: Path) -> list[str]:
        problems: list[str] = []
        if not any((directory / "functions").glob("*.py")):
            problems.append("The bundle must contain functions/*.py.")
        for name in PROJECT_LEVEL:
            if (directory / name).exists():
                problems.append(f"{name} cannot be deployed: it changes what the whole project enforces, so it is loaded from the project's mounted code, not from an upload.")
        for source in sorted(directory.rglob("*.py")):
            try:
                compile(source.read_text(), str(source), "exec")
            except SyntaxError as error:
                problems.append(f"{source.relative_to(directory)}:{error.lineno}: {error.msg}")
            except UnicodeDecodeError:
                problems.append(f"{source.relative_to(directory)} is not UTF-8 text.")
        return problems
