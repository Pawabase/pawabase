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
import os
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

from pawabase_server.functions import vendor as vendoring
from pawabase_server.functions.http_functions import HttpFunctionSpec, parse_config
from pawabase_server.functions.registry import (
    MAIN,
    ProjectCode,
    _loading_project,
    clear_functions,
    deployment_key,
    function,
    load_code_dir,
)
from pawabase_server.functions.sandbox import typescript

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

    def __init__(
        self, message: str, problems: list[str] | None = None, *, status: int = 422
    ) -> None:
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
            problems.append(
                f"{REQUIREMENTS}:{number}: {line!r} is not a plain requirement (a package name with optional version, such as 'Pillow>=10.3'; options, URLs and paths are not allowed)."
            )
        else:
            lines.append(line)
    if len(lines) > MAX_REQUIREMENTS:
        problems.append(
            f"{REQUIREMENTS} lists {len(lines)} packages; the limit is {MAX_REQUIREMENTS}."
        )
    return lines, problems


def requirements_in(archive: bytes) -> str | None:
    """The text of ``functions/requirements.txt`` inside a bundle, without unpacking it."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for member in bundle.getmembers():
                if (
                    member.name == REQUIREMENTS
                    and member.isfile()
                    and member.size <= MAX_FILE_BYTES
                ):
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
                if (
                    member.issym()
                    or member.islnk()
                    or member.isdev()
                    or path.is_absolute()
                    or ".." in path.parts
                ):
                    raise DeploymentError(f"The bundle contains an unsafe entry: {member.name}")
                if not member.isfile():
                    continue
                if member.size > MAX_FILE_BYTES:
                    raise DeploymentError(
                        f"{member.name} is larger than {MAX_FILE_BYTES // 1024 // 1024}MB."
                    )
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

    def __init__(
        self,
        root: str | Path,
        *,
        install_requirements: bool = True,
        index_url: str = "",
        install_timeout: int = 300,
        binary_only: bool = False,
        vendor: bool = True,
        vendor_timeout: int = 300,
        npm_registry: str = "",
        http_limits: dict[str, int] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        #: Fetches a TypeScript deployment's ``npm:``/``jsr:``/URL imports when it is activated (see ``functions/vendor.py``).
        self.vendor = vendoring.Vendor(
            self.root,
            enabled=vendor,
            timeout=vendor_timeout,
            npm_registry=npm_registry,
            kit=typescript.KIT,
        )
        #: What an HTTP function's ``config`` may ask for: the installation's ceilings.
        self.http_limits = {"timeout": 400, "memory": 512, "concurrency": 256, **(http_limits or {})}
        #: The HTTP functions of each loaded deployment, by registry key and name.
        self.http: dict[str, dict[str, HttpFunctionSpec]] = {}
        self.install_requirements = install_requirements
        self.index_url = index_url
        self.install_timeout = install_timeout
        self.binary_only = binary_only
        self._seen: dict[str, _Seen] = {}
        #: ``required`` or ``off``: how a TypeScript deployment is confined while it is described (the sandbox's own setting).
        self.confinement = "off"

    # ── where things are ────────────────────────────────────────────────

    def folder(self, env: str, branch: str = MAIN) -> Path:
        return self.root / env / (branch or MAIN)

    def current(self, env: str, branch: str = MAIN) -> Path:
        return self.folder(env, branch) / "current"

    def archive(self, env: str, branch: str, deployment_id: str) -> Path:
        return self.folder(env, branch) / "archives" / f"{deployment_id}.tar.gz"

    def vendor_cache(self, env: str, branch: str = MAIN) -> Path | None:
        """The vendored module cache of what is live for *env*/*branch*, or ``None`` when it imports nothing remote."""
        digest = (self.stamp(env, branch) or {}).get("vendor")
        return self.vendor.cache_dir(digest) if digest and self.vendor.ready(digest) else None

    def http_functions(self, env: str, branch: str = MAIN) -> dict[str, HttpFunctionSpec]:
        """The HTTP functions live for *env*/*branch* (loading the deployment if this process has not yet)."""
        self.ensure_loaded(env, branch)
        return dict(self.http.get(deployment_key(env, branch), {}))

    def http_function(self, env: str, name: str, branch: str | None = None) -> HttpFunctionSpec | None:
        """An HTTP function as *branch* sees it: the branch's deployment first, then the environment's."""
        for where in ([branch] if branch and branch != MAIN else []) + [MAIN]:
            found = self.http_functions(env, where).get(name)
            if found is not None:
                return found
        return None

    def language(self, env: str, branch: str = MAIN) -> str:
        """``python`` or ``typescript``: what the live deployment of *env*/*branch* is written in."""
        return (self.stamp(env, branch) or {}).get("language") or typescript.PYTHON

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
                [
                    "functions/requirements.txt is present, but installing is off here (PAWABASE_FUNCTION_INSTALL). Enable installing (PAWABASE_FUNCTION_INSTALL=true) or remove the file."
                ],
                status=409,
            )
        digest = hashlib.sha256(
            (
                "\n".join(sorted(lines))
                + "\n"
                + self.index_url
                + f"\npy{sys.version_info.major}.{sys.version_info.minor}"
            ).encode()
        ).hexdigest()[:16]
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
            (installed / ".ok").write_text(
                json.dumps({"requirements": lines, "installed_at": time.time()})
            )
            try:
                installed.rename(target)
            except OSError:  # another process finished the same set first
                if not (target / ".ok").is_file():
                    raise
        finally:
            shutil.rmtree(building, ignore_errors=True)
        return digest

    @staticmethod
    def _install_environment() -> dict[str, str]:
        """What the installer runs with: the way to the network and the package index, and nothing of the server's own configuration.

        A package's build script is code the customer chose. It must not be handed the master key, the database addresses or the
        internal secret just because the installer was started by the server.
        """
        keep = (
            "PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
        )  # fmt: skip
        env = {name: os.environ[name] for name in keep if name in os.environ}
        env.update({k: v for k, v in os.environ.items() if k.startswith(("PIP_", "UV_"))})
        env["HOME"] = env.get("TMPDIR", "/tmp")
        return env

    def _pip(self, requirements: Path, target: Path) -> None:
        index = ["--index-url", self.index_url] if self.index_url else []
        if self.binary_only:
            index = [*index, "--only-binary", ":all:"]
        uv = shutil.which("uv")
        if uv:
            command = [
                uv,
                "pip",
                "install",
                "--quiet",
                "--no-cache-dir",
                "--python",
                sys.executable,
                "--target",
                str(target),
                "-r",
                str(requirements),
                *index,
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--target",
                str(target),
                "-r",
                str(requirements),
                *index,
            ]
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.install_timeout,
                check=False,
                env=self._install_environment(),
            )  # noqa: S603 - argv list, lines are validated
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError(
                "Installing the requirements took too long.",
                [f"Stopped after {self.install_timeout}s."],
            ) from exc
        except OSError as exc:
            raise DeploymentError(
                "The installer could not be started.", [str(exc)], status=500
            ) from exc
        if done.returncode != 0:
            tail = [
                line for line in (done.stderr or done.stdout).strip().splitlines() if line.strip()
            ][-8:]
            raise DeploymentError(
                "The requirements could not be installed.",
                [
                    "functions/requirements.txt: " + (tail[0] if tail else "the installer failed"),
                    *tail[1:],
                ],
            )

    def libraries(self, env: str, branch: str = MAIN) -> list[Path]:
        """The installed libraries of what is live for *env*/*branch* (restored if the shared volume lost them)."""
        return self._library_paths(self.stamp(env, branch), self.current(env, branch))

    def _library_paths(self, stamp: dict[str, Any] | None, current: Path) -> list[Path]:
        digest = (stamp or {}).get("packages")
        if not digest:
            return []
        target = self.packages_dir(digest)
        if not (
            target / ".ok"
        ).is_file():  # the shared volume lost it: build it again from the artifact's own file
            try:
                self._ensure_packages((current / REQUIREMENTS).read_text())
            except (OSError, DeploymentError) as exc:
                logger.error(
                    "libraries for %s are missing and could not be restored: %s", current, exc
                )
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
            self.http.pop(key, None)
            seen.loaded_id, seen.code = None, None
            return None
        return self.load(env, branch)

    def load(self, env: str, branch: str = MAIN) -> ProjectCode:
        key = deployment_key(env, branch)
        seen = self._seen.setdefault(key, _Seen())
        clear_functions(key)
        self.http.pop(key, None)
        for name in [name for name in sys.modules if name.startswith(f"{module_prefix(key)}.")]:
            del sys.modules[name]
        stamp = self.stamp(env, branch)
        current = self.current(env, branch)
        if (stamp or {}).get("language") == typescript.TYPESCRIPT:
            code = self._load_typescript(key, current, env=env, branch=branch, stamp=stamp or {})
            seen.checked_at, seen.loaded_id, seen.code = (
                time.monotonic(),
                (stamp or {}).get("id"),
                code,
            )
            if code.errors:
                logger.warning("deployment %s has errors: %s", key, code.errors)
            return code
        # Forget modules an earlier artifact imported from its own files, but never the installed libraries: a C extension cannot be imported twice.
        owners = (
            [entry for entry in self.root.iterdir() if entry.is_dir() and entry.name != PACKAGES]
            if self.root.is_dir()
            else []
        )
        code = load_code_dir(
            current,
            key,
            only_functions=True,
            purge_under=owners,
            extra_paths=self._library_paths(stamp, current),
        )
        seen.checked_at, seen.loaded_id, seen.code = time.monotonic(), (stamp or {}).get("id"), code
        if code.errors:
            logger.warning("deployment %s has errors: %s", key, code.errors)
        return code

    def _load_typescript(
        self, key: str, current: Path, *, env: str, branch: str, stamp: dict[str, Any]
    ) -> ProjectCode:
        """Ask a confined Deno what the deployment defines, and register each function here so routing and policies see it.

        ``fn()`` functions are registered in the kit's registry with stand-in handlers (a TypeScript function always runs in its Deno
        worker, never in this process). HTTP functions are kept in :attr:`http`, with their checked ``config``.
        """
        digest = stamp.get("vendor")
        cache = self.vendor.cache_dir(digest) if digest and self.vendor.ready(digest) else None
        if digest and cache is None:
            return ProjectCode(
                project=key,
                errors=[
                    "The deployment's npm/jsr/URL imports are not on this server any more; deploy it again to fetch them."
                ],
            )
        try:
            answer = typescript.describe(
                current, environment=self.worker_environment(), confinement=self.confinement, cache=cache
            )
        except typescript.RuntimeUnavailable as exc:
            return ProjectCode(project=key, errors=[str(exc)])
        code = ProjectCode(project=key, errors=list(answer.get("errors") or []))
        token = _loading_project.set(key)
        try:
            for spec in answer.get("specs") or []:
                handler = _typescript_handler(spec["name"], spec.get("file"), spec.get("line"))
                function(
                    spec["name"],
                    description=str(spec.get("description") or ""),
                    policy=spec.get("policy", "authenticated"),
                    input_fields=spec.get("input_fields"),
                    timeout=float(spec.get("timeout") or 30),
                    tags=tuple(spec.get("tags") or ()),
                )(handler)
                code.functions.append(spec["name"])
        except (ValueError, TypeError) as exc:
            code.errors.append(f"A function definition was refused: {exc}")
        finally:
            _loading_project.reset(token)
        http: dict[str, HttpFunctionSpec] = {}
        for found in answer.get("http") or []:
            name = str(found.get("name"))
            config, problems = parse_config(
                found.get("config"),
                max_timeout=self.http_limits["timeout"],
                max_memory=self.http_limits["memory"],
                max_concurrency=self.http_limits["concurrency"],
            )
            if problems:
                code.errors.extend(f"{found.get('entry')}: {problem}" for problem in problems)
                continue
            if name in code.functions:
                code.errors.append(
                    f"{found.get('entry')}: an fn() function is also called {name!r}; one name, one function."
                )
                continue
            http[name] = HttpFunctionSpec(
                name=name,
                entry=str(found.get("entry")),
                config=config,
                style=str(found.get("style") or "pawabase"),
                env=env,
                branch=branch,
                deployment_id=stamp.get("id"),
                vendor=digest,
            )
        self.http[key] = http
        return code

    @staticmethod
    def worker_environment() -> dict[str, str]:
        """What a worker or describe process starts with: no secrets, no database address."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            **(
                {"PAWABASE_DENO_PATH": os.environ["PAWABASE_DENO_PATH"]}
                if os.environ.get("PAWABASE_DENO_PATH")
                else {}
            ),
        }

    # ── installing ──────────────────────────────────────────────────────

    def install(
        self,
        env: str,
        branch: str,
        archive: bytes,
        *,
        deployment_id: str,
        checksum: str,
        keep_archive: bool = True,
    ) -> ProjectCode:
        """Activate *archive*, or raise :class:`DeploymentError` having changed nothing that is served."""
        folder = self.folder(env, branch)
        folder.mkdir(parents=True, exist_ok=True)
        staging = folder / f".staging-{deployment_id}"
        previous = folder / f".previous-{deployment_id}"
        current = folder / "current"
        try:
            files = unpack(archive, staging)
            language = typescript.language_of(
                [p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()]
            )
            if language == "mixed":
                raise DeploymentError(
                    "A deployment is one language.",
                    [
                        "functions/ holds both .py and .ts files: deploy either Python or TypeScript functions."
                    ],
                )
            problems = self._check(staging, language or typescript.PYTHON)
            if problems:
                raise DeploymentError("The bundle was refused.", problems)
            packages = self.prepare(archive) if language != typescript.TYPESCRIPT else None
            vendored = (
                self._vendor(staging, checksum) if language == typescript.TYPESCRIPT else None
            )
            (staging / STAMP).write_text(
                json.dumps(
                    {
                        "id": deployment_id,
                        "checksum": checksum,
                        "files": files,
                        "packages": packages,
                        "language": language or typescript.PYTHON,
                        "vendor": vendored.digest if vendored else None,
                        "remote_imports": vendored.specifiers if vendored else [],
                        "activated_at": time.time(),
                    }
                )
            )
            if current.exists():
                current.rename(previous)
            staging.rename(current)
            code = self.load(env, branch)
            if code.errors:
                raise DeploymentError(
                    "The new code does not load, so it was not activated.", code.errors
                )
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

    def _vendor(self, staging: Path, checksum: str) -> vendoring.VendorResult:
        """Fetch the remote imports of an unpacked TypeScript deployment, or raise :class:`DeploymentError` saying why it cannot be."""
        try:
            deno = typescript.deno_path()
        except typescript.RuntimeUnavailable as exc:
            raise DeploymentError(str(exc), status=500) from exc
        http = [
            {"name": path.parent.name, "entry": path.relative_to(staging).as_posix()}
            for path in sorted((staging / "functions").glob("*/index.*"))
            if typescript.is_http_entry(path.relative_to(staging).as_posix())
        ]
        try:
            return self.vendor.vendor(staging, checksum, deno=deno, http_functions=http)
        except vendoring.VendorError as exc:
            raise DeploymentError(exc.message, exc.problems) from exc

    def reactivate(self, env: str, branch: str, deployment_id: str) -> ProjectCode:
        """Put an earlier artifact back (a rollback). Raises :class:`DeploymentError` when its bytes are gone."""
        source = self.archive(env, branch, deployment_id)
        if not source.is_file():
            raise DeploymentError("That deployment's artifact is no longer stored.", status=409)
        data = source.read_bytes()
        return self.install(
            env,
            branch,
            data,
            deployment_id=deployment_id,
            checksum=hashlib.sha256(data).hexdigest(),
            keep_archive=False,
        )

    def remove_branch(self, env: str, branch: str) -> bool:
        if branch == MAIN:
            raise DeploymentError(
                "The main deployment is replaced by deploying again, not removed.", status=409
            )
        folder = self.folder(env, branch)
        if not folder.exists():
            return False
        clear_functions(deployment_key(env, branch))
        self.http.pop(deployment_key(env, branch), None)
        self._seen.pop(deployment_key(env, branch), None)
        shutil.rmtree(folder)
        return True

    @staticmethod
    def _check(directory: Path, language: str = "python") -> list[str]:
        problems: list[str] = []
        if language == typescript.TYPESCRIPT:
            names = [p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()]
            if not any(
                p.suffix in typescript.SOURCES for p in (directory / "functions").glob("*")
            ) and not any(typescript.is_http_entry(n) for n in names):
                problems.append("The bundle must contain functions/*.ts or functions/<name>/index.ts.")
            if (directory / "functions" / "requirements.txt").exists():
                problems.append(
                    "functions/requirements.txt is for Python; a TypeScript deployment carries its own code."
                )
            return problems
        if not any((directory / "functions").glob("*.py")):
            problems.append("The bundle must contain functions/*.py.")
        for name in PROJECT_LEVEL:
            if (directory / name).exists():
                problems.append(
                    f"{name} cannot be deployed: it changes what the whole runtime enforces, so it is loaded from the mounted code, not from an upload."
                )
        for source in sorted(directory.rglob("*.py")):
            try:
                compile(source.read_text(), str(source), "exec")
            except SyntaxError as error:
                problems.append(f"{source.relative_to(directory)}:{error.lineno}: {error.msg}")
            except UnicodeDecodeError:
                problems.append(f"{source.relative_to(directory)} is not UTF-8 text.")
        return problems


def _typescript_handler(name: str, file: str | None = None, line: int | None = None) -> Any:
    async def handler(ctx: Any) -> Any:
        raise RuntimeError(
            f"{name} is a TypeScript function; it runs in its Deno worker, not in this process"
        )

    handler.typescript = True  # type: ignore[attr-defined]
    #: Where the function is defined in the deployment (``functions/demo.ts``, line), for Studio's code view.
    handler.source = (file, line)  # type: ignore[attr-defined]
    return handler
