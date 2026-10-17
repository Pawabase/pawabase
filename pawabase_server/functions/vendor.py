"""Imports of a TypeScript deployment: import maps, and ``npm:``/``jsr:``/URL modules fetched once, when the deployment is activated.

A function's code may import

* local files, relatively or through an import map (``functions/deno.json`` for every function, ``functions/<name>/deno.json`` for one
  HTTP function, as Supabase projects lay them out; an ``import_map.json`` beside them works too);
* the kit, as ``"@pawabase/functions"`` or ``"pawabase"``;
* ``npm:``, ``jsr:`` and ``https://`` modules, directly or through the import map.

Remote modules are **vendored**: when a deployment is activated, Deno downloads every module its entry points reach into a cache that
belongs to that deployment (``<deployments>/.deno/<checksum>/``). Workers then run with ``--cached-only``: they never fetch anything, so a
deployment runs exactly the code that was fetched for it, a registry outage cannot break a cold start, and a worker needs no network to
load. A deployment with remote imports is refused when vendoring is turned off (``PAWABASE_FUNCTION_VENDOR=false``), rather than failing
on its first request.

The cache is read-only to workers. Deno also wants to write next to its cache (compiled-code and analysis databases), so each worker gets
a ``DENO_DIR`` of its own in its temporary folder whose ``remote``/``npm`` entries are links into the shared, read-only cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawabase.deployments")

VENDOR_DIR = ".deno"
OK = ".ok"
#: What a function imports the kit as.
KIT_SPECIFIERS = ("@pawabase/functions", "pawabase")
SOURCE_SUFFIXES = (".ts", ".mts", ".js", ".mjs", ".tsx", ".jsx")
CONFIG_NAMES = ("deno.json", "deno.jsonc", "import_map.json")
REMOTE_PREFIXES = ("npm:", "jsr:", "https://", "http://")
#: ``import … from "x"``, ``export … from "x"``, ``import("x")``, ``import "x"``. Good enough to know whether a deployment needs vendoring;
#: Deno itself resolves the real graph when it vendors.
SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)(["'])((?:npm:|jsr:|https?://)[^"'\s]+)\1"""
)
#: Cached subfolders a worker may read from the vendored cache (everything else Deno writes goes to the worker's own folder).
SHARED_CACHE_ENTRIES = ("remote", "npm", "registries")


class VendorError(Exception):
    """The deployment's imports cannot be resolved. ``problems`` says why, one line each."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.message, self.problems = message, problems or []


def _strip_jsonc(text: str) -> str:
    """``deno.jsonc`` without its comments (strings are respected) and without trailing commas."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def read_config(path: Path) -> dict[str, Any]:
    """A ``deno.json(c)`` or ``import_map.json`` as a dict. Raises :class:`VendorError` for one that is not valid JSON."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(_strip_jsonc(raw) if path.suffix == ".jsonc" else raw)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VendorError(f"{path.name} cannot be read.", [f"{path}: {exc}"]) from exc
    if not isinstance(data, dict):
        raise VendorError(f"{path.name} must be a JSON object.")
    return data


def _resolve_target(target: str, base: Path, artifact: Path) -> str:
    """An import map target as a URL: relative paths become ``file://`` URLs inside the deployment, remote ones stay as they are."""
    if target.startswith(REMOTE_PREFIXES) or target.startswith("node:"):
        return target
    if target.startswith("file://"):
        raise VendorError("An import map may not point at absolute files.", [target])
    if not target.startswith(("./", "../", "/")):
        return target  # a bare specifier mapped onto another entry of the map
    resolved = (base / target).resolve() if not target.startswith("/") else Path(target)
    if not resolved.is_relative_to(artifact.resolve()):
        raise VendorError("An import map points outside the deployment.", [f"{target} (from {base})"])
    url = resolved.as_uri()
    return url + "/" if target.endswith("/") and not url.endswith("/") else url


def _imports_of(config: dict[str, Any], base: Path, artifact: Path) -> dict[str, str]:
    imports = config.get("imports") or {}
    if not isinstance(imports, dict):
        raise VendorError("`imports` must be an object of specifier → target.")
    return {str(key): _resolve_target(str(value), base, artifact) for key, value in imports.items()}


def _scopes_of(config: dict[str, Any], base: Path, artifact: Path) -> dict[str, dict[str, str]]:
    scopes = config.get("scopes") or {}
    if not isinstance(scopes, dict):
        return {}
    return {
        _resolve_target(str(scope), base, artifact): _imports_of({"imports": mapping}, base, artifact)
        for scope, mapping in scopes.items()
        if isinstance(mapping, dict)
    }


def _configs_in(folder: Path) -> list[Path]:
    return [folder / name for name in CONFIG_NAMES if (folder / name).is_file()]


def import_map(artifact: Path, kit: Path, function: str | None = None) -> dict[str, Any]:
    """The import map a worker runs with: the kit, then ``functions/``'s map, then (for one HTTP function) that function's own map.

    A later map wins for a specifier both name. The kit's names cannot be remapped: a deployment must not swap the platform's kit.
    """
    imports: dict[str, str] = {}
    scopes: dict[str, dict[str, str]] = {}
    folders = [artifact / "functions"]
    if function:
        folders.append(artifact / "functions" / function)
    for folder in folders:
        for path in _configs_in(folder):
            config = read_config(path)
            if path.name == "import_map.json" or "imports" in config or "scopes" in config:
                imports.update(_imports_of(config, folder, artifact))
                for scope, mapping in _scopes_of(config, folder, artifact).items():
                    scopes.setdefault(scope, {}).update(mapping)
    for name in KIT_SPECIFIERS:
        imports[name] = (kit / "pawabase.ts").as_uri()
    result: dict[str, Any] = {"imports": imports}
    if scopes:
        result["scopes"] = scopes
    return result


def sources(artifact: Path) -> list[Path]:
    root = artifact / "functions"
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and not path.name.endswith(".d.ts")
        and "node_modules" not in path.parts
    )


def remote_specifiers(artifact: Path, kit: Path) -> list[str]:
    """Every ``npm:``, ``jsr:`` and URL specifier the deployment's sources or import maps name (sorted, unique)."""
    found: set[str] = set()
    for path in sources(artifact):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.update(match.group(2) for match in SPECIFIER.finditer(text))
    folders = [artifact / "functions"] + [
        entry for entry in (artifact / "functions").iterdir() if entry.is_dir()
    ] if (artifact / "functions").is_dir() else []
    for folder in folders:
        for path in _configs_in(folder):
            try:
                config = read_config(path)
            except VendorError:
                continue
            for target in (config.get("imports") or {}).values():
                if isinstance(target, str) and target.startswith(REMOTE_PREFIXES):
                    found.add(target)
    return sorted(found)


def entry_points(artifact: Path, http_functions: list[dict[str, Any]] | None = None) -> list[tuple[str | None, Path]]:
    """``(function or None, entry file)`` for everything a deployment's workers import: the ``fn()`` files, and each HTTP function."""
    root = artifact / "functions"
    out: list[tuple[str | None, Path]] = []
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES and not path.name.endswith(".d.ts") and ".test." not in path.name:
                out.append((None, path))
    for spec in http_functions or []:
        out.append((spec["name"], artifact / spec["entry"]))
    return out


@dataclass
class VendorResult:
    """What vendoring produced: the cache's digest (``None`` when nothing remote is imported), and the specifiers found."""

    digest: str | None
    specifiers: list[str] = field(default_factory=list)
    seconds: float = 0.0


class Vendor:
    """Fetches a deployment's remote modules into ``<root>/.deno/<digest>/`` and gives workers what they need to run from it."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool,
        timeout: int = 300,
        npm_registry: str = "",
        kit: Path,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.timeout = timeout
        self.npm_registry = npm_registry
        self.kit = kit

    def cache_dir(self, digest: str) -> Path:
        return self.root / VENDOR_DIR / digest

    def ready(self, digest: str | None) -> bool:
        return bool(digest) and (self.cache_dir(digest) / OK).is_file()

    @staticmethod
    def digest_for(checksum: str, specifiers: list[str]) -> str:
        """One cache per deployment's bytes: a rollback to the same bytes reuses it."""
        return hashlib.sha256((checksum + "\n" + "\n".join(specifiers)).encode()).hexdigest()[:32]

    def vendor(
        self,
        artifact: Path,
        checksum: str,
        *,
        deno: str,
        http_functions: list[dict[str, Any]] | None = None,
    ) -> VendorResult:
        """Fetch every remote module the deployment's entry points reach. Blocking: run it off the event loop.

        Raises :class:`VendorError` when the deployment imports remote modules and they cannot be fetched (or vendoring is off).
        """
        specifiers = remote_specifiers(artifact, self.kit)
        if not specifiers:
            return VendorResult(None, [])
        if not self.enabled:
            raise VendorError(
                "This deployment imports npm:, jsr: or URL modules, and this server does not fetch them.",
                [*(f"imports {s}" for s in specifiers[:10]), "Set PAWABASE_FUNCTION_VENDOR=true on the server, or bundle the code locally."],
            )
        digest = self.digest_for(checksum, specifiers)
        target = self.cache_dir(digest)
        if self.ready(digest):
            return VendorResult(digest, specifiers)
        started = time.monotonic()
        staging = Path(tempfile.mkdtemp(prefix="vendor-", dir=self._parent()))
        try:
            for function, entry in entry_points(artifact, http_functions):
                self._install(deno, artifact, entry, function, staging)
            (staging / OK).write_text(json.dumps({"specifiers": specifiers, "at": time.time()}))
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        seconds = time.monotonic() - started
        logger.info("vendored %d remote imports in %.1fs into %s", len(specifiers), seconds, target)
        return VendorResult(digest, specifiers, seconds)

    def _parent(self) -> Path:
        parent = self.root / VENDOR_DIR
        parent.mkdir(parents=True, exist_ok=True)
        return parent

    def _install(self, deno: str, artifact: Path, entry: Path, function: str | None, cache: Path) -> None:
        mapping = import_map(artifact, self.kit, function)
        with tempfile.TemporaryDirectory(prefix="vendor-map-") as scratch:
            map_file = Path(scratch) / "import_map.json"
            map_file.write_text(json.dumps(mapping))
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": scratch,
                "DENO_DIR": str(cache),
                "DENO_NO_UPDATE_CHECK": "1",
                "NO_COLOR": "1",
            }
            if self.npm_registry:
                environment["NPM_CONFIG_REGISTRY"] = self.npm_registry
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "DENO_CERT"):
                if os.environ.get(name):
                    environment[name] = os.environ[name]
            command = [
                deno,
                "cache",
                "--quiet",
                "--no-config",
                "--no-lock",
                "--node-modules-dir=none",
                f"--import-map={map_file}",
                str(entry),
            ]
            try:
                done = subprocess.run(  # noqa: S603 - argv list; the entry is a path inside the unpacked deployment
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=environment,
                    cwd=scratch,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise VendorError(
                    "Fetching the deployment's imports took too long.", [f"Stopped after {self.timeout}s."]
                ) from exc
            except OSError as exc:
                raise VendorError("Deno could not be started to fetch the imports.", [str(exc)]) from exc
        if done.returncode != 0:
            lines = [line for line in (done.stderr or done.stdout).splitlines() if line.strip()][-8:]
            where = entry.relative_to(artifact).as_posix()
            raise VendorError(
                f"The imports of {where} could not be fetched.",
                [f"{where}: {lines[0] if lines else 'deno cache failed'}", *lines[1:]],
            )

    def worker_dir(self, digest: str | None, tmp: Path) -> Path:
        """A ``DENO_DIR`` for one worker: its own folder, with the vendored modules linked in read-only."""
        own = tmp / "deno"
        own.mkdir(parents=True, exist_ok=True)
        if digest and self.ready(digest):
            shared = self.cache_dir(digest)
            for name in SHARED_CACHE_ENTRIES:
                source = shared / name
                link = own / name
                if source.exists() and not link.exists():
                    link.symlink_to(source, target_is_directory=True)
        return own

    def remove_unused(self, keep: set[str]) -> int:
        """Delete vendored caches no deployment on this server uses any more. Returns how many were removed."""
        parent = self.root / VENDOR_DIR
        if not parent.is_dir():
            return 0
        removed = 0
        for entry in parent.iterdir():
            if entry.is_dir() and entry.name not in keep and not entry.name.startswith("vendor-"):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed
