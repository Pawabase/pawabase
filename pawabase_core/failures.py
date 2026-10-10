"""Describing a failure: what broke, where in your code, and what to try.

A Python traceback of a failed request is mostly the web framework, its middleware, the ORM and the database driver. This keeps the part that is
yours: :func:`report` turns an exception into a :class:`Failure` with its kind, message, the few frames of your code behind it and a hint, and
:meth:`Failure.trace` prints those frames in place of the whole traceback.
"""

from __future__ import annotations

import os
import re
import site
import sysconfig
import traceback
from dataclasses import dataclass, field

#: Frames shown in a location, innermost first. More than this is the framework talking.
MAX_FRAMES = 4
#: Longest message kept, in log lines and in stored requests.
MAX_MESSAGE = 400

_LIBRARY_ROOTS: tuple[str, ...] = tuple(
    {
        os.path.realpath(path)
        for path in (
            sysconfig.get_paths().get("stdlib"),
            sysconfig.get_paths().get("platstdlib"),
            sysconfig.get_paths().get("purelib"),
            sysconfig.get_paths().get("platlib"),
            *site.getsitepackages(),
        )
        if path
    }
)
#: A deployed function lives at <deployments>/<environment>/<branch>/current/<file>: this recovers `<file>`.
_DEPLOYED = re.compile(r".*/deployments/[^/]+/[^/]+/current/")
#: Where Pawabase's own code is installed in the container.
_INSTALLED = re.compile(r"^/app/")


@dataclass(frozen=True)
class Where:
    """One place in your code, as a person would say it: ``functions/overview.py:28 in overview``."""

    file: str
    line: int
    function: str
    code: str | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line} in {self.function}"


@dataclass(frozen=True)
class Failure:
    """What broke, in words: the kind of error, its message, where in your code, and what to try."""

    kind: str
    message: str
    where: list[Where] = field(default_factory=list)
    hint: str | None = None

    @property
    def summary(self) -> str:
        return f"{self.kind}: {self.message}" if self.message else self.kind

    @property
    def location(self) -> str | None:
        return str(self.where[0]) if self.where else None

    def line(self) -> str:
        """The failure as one log line: ``OperationalError: relation "x" does not exist at functions/a.py:28 in overview``."""
        return f"{self.summary} at {self.location}" if self.where else self.summary

    def trace(self) -> str:
        """The few frames of your code that led to it, outermost first, then the error: what a person reads to find the line."""
        lines = []
        for place in reversed(self.where):
            lines.append(f"  {place}")
            if place.code:
                lines.append(f"    {place.code}")
        lines.append(self.summary)
        return "\n".join(lines)


#: What a known failure usually means, and what to do. The first pattern that matches wins.
_HINTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), hint)
    for pattern, hint in (
        (
            r'relation "([^"]+)" does not exist|no such table: (\w+)|table \S*?(\w+)\S* doesn\'t exist',
            "That table has not been created in this environment's database. Create it with Migrate on the resource in Studio.",
        ),
        (
            r"password authentication failed|authentication failed for user",
            "The database refused the username or password in its connection URL.",
        ),
        (
            r"connection refused|could not connect|name or service not known|nodename nor servname|network is unreachable|connect call failed",
            "The database (or the service it talks to) could not be reached. Check that it is running and that its address is right.",
        ),
        (
            r"too many connections|remaining connection slots|too many clients",
            "The database has no connection left. Lower PAWABASE_DB_POOL_MAX or put PgBouncer in front of it.",
        ),
        (
            r"ssl|certificate verify failed",
            "The secure connection to the server failed. Check its certificate and that the URL asks for TLS the way the server expects.",
        ),
        (
            r"duplicate key value|unique constraint|UNIQUE constraint failed",
            "A record with the same unique value already exists.",
        ),
        (
            r"violates foreign key|FOREIGN KEY constraint failed",
            "The record refers to another record that does not exist, or is still referred to by one.",
        ),
        (
            r"permission denied|read-only|readonly",
            "The database role is not allowed to do that.",
        ),
        (
            r"timeout|timed out|deadline exceeded",
            "It took longer than allowed. Try a smaller request, or raise the function's timeout.",
        ),
    )
)


def hint_for(text: str) -> str | None:
    """What usually fixes an error whose message reads like *text*, or ``None``."""
    for pattern, hint in _HINTS:
        if pattern.search(text):
            return hint
    return None


def root_cause(exc: BaseException) -> BaseException:
    """The error at the bottom of a chain of ``raise ... from ...``: the one that actually happened."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        nxt = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        if nxt is None:
            break
        exc = nxt
    return exc


def _is_library(path: str) -> bool:
    if path.startswith("<"):  # <frozen importlib._bootstrap>, <string>
        return True
    real = os.path.realpath(path)
    return any(real.startswith(root + os.sep) for root in _LIBRARY_ROOTS)


def _short(path: str) -> str:
    """A path as a person would write it: relative to the deployed function or to the installation, else the last two parts."""
    shortened = _DEPLOYED.sub("", path)
    if shortened != path:
        return shortened
    shortened = _INSTALLED.sub("", path)
    if shortened != path:
        return shortened
    parts = path.rsplit("/", 2)
    return "/".join(parts[-2:])


def locate(exc: BaseException, *, roots: tuple[str, ...] = ()) -> list[Where]:
    """The frames of your code behind *exc*, innermost first, without the libraries between them.

    With *roots* (directory prefixes, such as where deployed functions live) only frames below one of them count when there are any; a
    failure in Pawabase's own code has none there and falls back to every frame that is not a library.
    """
    cause = root_cause(exc)
    frames = [
        frame
        for frame in traceback.extract_tb(cause.__traceback__)
        if not _is_library(frame.filename)
    ]
    if roots:
        mine = [f for f in frames if any(f.filename.startswith(root) for root in roots)]
        frames = mine or frames
    return [
        Where(_short(f.filename), f.lineno or 0, f.name, (f.line or "").strip() or None)
        for f in reversed(frames[-MAX_FRAMES * 2 :])
    ][:MAX_FRAMES]


def report(exc: BaseException, *, roots: tuple[str, ...] = ()) -> Failure:
    """Describe *exc*: what it was, where in your code, what to try."""
    carried = getattr(exc, "failure", None)
    if isinstance(
        carried, Failure
    ):  # a failure that was worked out elsewhere (a sandboxed function's), with its own frames
        return carried
    cause = root_cause(exc)
    kind = type(cause).__name__
    message = " ".join(str(cause).split())[:MAX_MESSAGE]
    hint = getattr(exc, "hint", None) or hint_for(f"{kind} {message}")
    return Failure(kind, message, locate(exc, roots=roots), hint)


__all__ = ["Failure", "Where", "hint_for", "locate", "report", "root_cause"]
