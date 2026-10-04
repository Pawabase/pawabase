"""Function artifacts on disk: what ``pawabase deploy`` uploaded, per environment and branch.

::

    <deployments_path>/<env>/<branch>/
        current/                       the active artifact, unpacked (functions/*.py and the helper packages shipped with it)
        current/.pawabase-deployment   {"id", "checksum", "packages"} of the active deployment: the one thing every process reads
        current/functions/requirements.txt   optional: the libraries the functions import, installed when the deployment is activated
        archives/<deployment id>.tar.gz   every artifact ever deployed, so a rollback re-activates bytes that really ran before

Libraries are installed once per distinct ``requirements.txt`` into ``<deployments_path>/.packages/<hash>/`` and put on ``sys.path`` only while
that environment's functions are imported. A deploy that changes only code reuses them; a rollback finds the ones its own deployment used.

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
import re
import shutil
import subprocess
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
REQUIREMENTS = "functions/requirements.txt"
PACKAGES = ".packages"
MAX_REQUIREMENTS = 100
#: One plain requirement: a name, optional extras, version specifiers, an optional environment marker. Nothing that reaches outside the index
#: (URLs, paths, VCS references, ``-r``/``-e``/``--index-url`` options): an upload must not choose where code comes from.
REQUIREMENT_LINE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9_,.\- ]+\])?\s*((===|==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+\s*,?\s*)*(;[^#]*)?$"
)
#: Loaded for the whole runtime from the mounted code directory, never from an upload: they change what the platform enforces and routes, not what a function does.
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


def parse_requirements(text: str) -> tuple[list[str], list[str]]:
    """The requirement lines in *text* and a problem for each line that is not a plain requirement."""
    lines: list[str] = []
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        if not REQUIREMENT_LINE.match(line):
            problems.append(f"{REQUIREMENTS}:{number}: {line!r} is not a plain requirement (a package name with optional version, such as 'Pillow>=10.3'; options, URLs and paths are not allowed).")
        else:
            lines.append(line)
    if len(lines) > MAX_REQUIREMENTS:
        problems.append(f"{REQUIREMENTS} lists {len(lines)} packages; the limit is {MAX_REQUIREMENTS}.")
    return lines, problems


def requirements_in(archive: bytes) -> str | None:
    """The text of ``functions/requirements.txt`` inside a bundle, without unpacking it."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for member in bundle.getmembers():
                if member.name == REQUIREMENTS and member.isfile() and member.size <= MAX_FILE_BYTES:
                    handle = bundle.extractfile(member)
                    return handle.read().decode("utf-8", "replace") if handle else None
    except tarfile.TarError:
        return None
    return None


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

    def __init__(self, root: str | Path, *, install_requirements: bool = True, index_url: str = "", install_timeout: int = 300) -> None:
        self.root = Path(root).resolve()
        self.install_requirements = install_requirements
        self.index_url = index_url
        self.install_timeout = install_timeout
        self._seen: dict[str, _Seen] = {}

    # ── where things are ────────────────────────────────────────────────

    def folder(self, env: str, branch: str = MAIN) -> Path:
        return self.root / env / (branch or MAIN)

    def current(self, env: str, branch: str = MAIN) -> Path:
        return self.folder(env, branch) / "current"

    def archive(self, env: str, branch: str, deployment_id: str) -> Path:
        return self.folder(env, branch) / "archives" / f"{deployment_id}.tar.gz"

    def stamp(self, env: str, branch: str = MAIN) -> dict[str, Any] | None:
        try:
            return json.loads((self.current(env, branch) / STAMP).read_text())
        except (OSError, ValueError):
            return None

    def branches(self, env: str) -> list[str]:
        base = self.root / env
        if not base.is_dir():
            return []
        return sorted(entry.name for entry in base.iterdir() if (entry / "current").is_dir())

    # ── libraries ───────────────────────────────────────────────────────

    def packages_dir(self, digest: str) -> Path:
        return self.root / PACKAGES / digest

    def prepare(self, archive: bytes) -> str | None:
        """Install what the bundle's ``functions/requirements.txt`` asks for (blocking: run it off the event loop). Returns the hash that names the
        installed set, or ``None`` when the bundle has none. Raises :class:`DeploymentError` when it cannot be installed; nothing is activated then."""
        text = requirements_in(archive)
        return None if text is None else self._ensure_packages(text)

    def _ensure_packages(self, text: str) -> str | None:
        lines, problems = parse_requirements(text)
        if problems:
            raise DeploymentError("functions/requirements.txt was refused.", problems)
        if not lines:
            return None
        if not self.install_requirements:
            raise DeploymentError(
                "This platform does not install a deployment's requirements.",
                ["functions/requirements.txt is present, but installing is off here (PAWABASE_FUNCTION_INSTALL). Enable installing (PAWABASE_FUNCTION_INSTALL=true) or remove the file."],
                status=409,
            )
        digest = hashlib.sha256(("\n".join(sorted(lines)) + "\n" + self.index_url + f"\npy{sys.version_info.major}.{sys.version_info.minor}").encode()).hexdigest()[:16]
        target = self.packages_dir(digest)
        if (target / ".ok").is_file():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        building = target.parent / f".build-{digest}-{time.monotonic_ns()}"
        building.mkdir()
        try:
            requirements = building / "requirements.txt"
            requirements.write_text("\n".join(lines) + "\n")
            installed = building / "site"
            self._pip(requirements, installed)
            (installed / ".ok").write_text(json.dumps({"requirements": lines, "installed_at": time.time()}))
            try:
                installed.rename(target)
            except OSError:  # another process finished the same set first
                if not (target / ".ok").is_file():
                    raise
        finally:
            shutil.rmtree(building, ignore_errors=True)
        return digest

    def _pip(self, requirements: Path, target: Path) -> None:
        index = ["--index-url", self.index_url] if self.index_url else []
        uv = shutil.which("uv")
        if uv:
            command = [uv, "pip", "install", "--quiet", "--no-cache-dir", "--python", sys.executable, "--target", str(target), "-r", str(requirements), *index]
        else:
            command = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(target), "-r", str(requirements), *index]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=self.install_timeout, check=False)  # noqa: S603 - argv list, lines are validated
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError("Installing the requirements took too long.", [f"Stopped after {self.install_timeout}s."]) from exc
        except OSError as exc:
            raise DeploymentError("The installer could not be started.", [str(exc)], status=500) from exc
        if done.returncode != 0:
            tail = [line for line in (done.stderr or done.stdout).strip().splitlines() if line.strip()][-8:]
            raise DeploymentError("The requirements could not be installed.", ["functions/requirements.txt: " + (tail[0] if tail else "the installer failed"), *tail[1:]])

    def _library_paths(self, stamp: dict[str, Any] | None, current: Path) -> list[Path]:
        digest = (stamp or {}).get("packages")
        if not digest:
            return []
        target = self.packages_dir(digest)
        if not (target / ".ok").is_file():  # the shared volume lost it: build it again from the artifact's own file
            try:
                self._ensure_packages((current / REQUIREMENTS).read_text())
            except (OSError, DeploymentError) as exc:
                logger.error("libraries for %s are missing and could not be restored: %s", current, exc)
                return []
        return [target]

    # ── loading ─────────────────────────────────────────────────────────

    def ensure_loaded(self, env: str, branch: str = MAIN) -> ProjectCode | None:
        """Make this process serve what is currently deployed. Cheap: one small file read, at most once a second per deployment."""
        key = deployment_key(env, branch)
        seen = self._seen.setdefault(key, _Seen())
        now = time.monotonic()
        if now - seen.checked_at < RECHECK_SECONDS:
            return seen.code
        seen.checked_at = now
        stamp = self.stamp(env, branch)
        wanted = stamp["id"] if stamp else None
        if wanted == seen.loaded_id:
            return seen.code
        if wanted is None:
            clear_functions(key)
            seen.loaded_id, seen.code = None, None
            return None
        return self.load(env, branch)

    def load(self, env: str, branch: str = MAIN) -> ProjectCode:
        key = deployment_key(env, branch)
        seen = self._seen.setdefault(key, _Seen())
        clear_functions(key)
        for name in [name for name in sys.modules if name.startswith(f"{module_prefix(key)}.")]:
            del sys.modules[name]
        stamp = self.stamp(env, branch)
        current = self.current(env, branch)
        # Forget modules an earlier artifact imported from its own files, but never the installed libraries: a C extension cannot be imported twice.
        owners = [entry for entry in self.root.iterdir() if entry.is_dir() and entry.name != PACKAGES] if self.root.is_dir() else []
        code = load_code_dir(current, key, only_functions=True, purge_under=owners, extra_paths=self._library_paths(stamp, current))
        seen.checked_at, seen.loaded_id, seen.code = time.monotonic(), (stamp or {}).get("id"), code
        if code.errors:
            logger.warning("deployment %s has errors: %s", key, code.errors)
        return code

    # ── installing ──────────────────────────────────────────────────────

    def install(self, env: str, branch: str, archive: bytes, *, deployment_id: str, checksum: str, keep_archive: bool = True) -> ProjectCode:
        """Activate *archive*, or raise :class:`DeploymentError` having changed nothing that is served."""
        folder = self.folder(env, branch)
        folder.mkdir(parents=True, exist_ok=True)
        staging = folder / f".staging-{deployment_id}"
        previous = folder / f".previous-{deployment_id}"
        current = folder / "current"
        try:
            files = unpack(archive, staging)
            problems = self._check(staging)
            if problems:
                raise DeploymentError("The bundle was refused.", problems)
            packages = self.prepare(archive)
            (staging / STAMP).write_text(json.dumps({"id": deployment_id, "checksum": checksum, "files": files, "packages": packages, "activated_at": time.time()}))
            if current.exists():
                current.rename(previous)
            staging.rename(current)
            code = self.load(env, branch)
            if code.errors:
                raise DeploymentError("The new code does not load, so it was not activated.", code.errors)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if previous.exists():
                if current.exists():
                    shutil.rmtree(current, ignore_errors=True)
                previous.rename(current)
                self.load(env, branch)
            elif current.exists() and not self.stamp(env, branch):
                shutil.rmtree(current, ignore_errors=True)
            raise
        finally:
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
        if keep_archive:
            target = self.archive(env, branch, deployment_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive)
        return code

    def reactivate(self, env: str, branch: str, deployment_id: str) -> ProjectCode:
        """Put an earlier artifact back (a rollback). Raises :class:`DeploymentError` when its bytes are gone."""
        source = self.archive(env, branch, deployment_id)
        if not source.is_file():
            raise DeploymentError("That deployment's artifact is no longer stored.", status=409)
        data = source.read_bytes()
        return self.install(env, branch, data, deployment_id=deployment_id, checksum=hashlib.sha256(data).hexdigest(), keep_archive=False)

    def remove_branch(self, env: str, branch: str) -> bool:
        if branch == MAIN:
            raise DeploymentError("The main deployment is replaced by deploying again, not removed.", status=409)
        folder = self.folder(env, branch)
        if not folder.exists():
            return False
        clear_functions(deployment_key(env, branch))
        self._seen.pop(deployment_key(env, branch), None)
        shutil.rmtree(folder)
        return True

    @staticmethod
    def _check(directory: Path) -> list[str]:
        problems: list[str] = []
        if not any((directory / "functions").glob("*.py")):
            problems.append("The bundle must contain functions/*.py.")
        for name in PROJECT_LEVEL:
            if (directory / name).exists():
                problems.append(f"{name} cannot be deployed: it changes what the whole runtime enforces, so it is loaded from the mounted code, not from an upload.")
        for source in sorted(directory.rglob("*.py")):
            try:
                compile(source.read_text(), str(source), "exec")
            except SyntaxError as error:
                problems.append(f"{source.relative_to(directory)}:{error.lineno}: {error.msg}")
            except UnicodeDecodeError:
                problems.append(f"{source.relative_to(directory)} is not UTF-8 text.")
        return problems
