"""What the agent and the control plane say to each other, and how each side knows the other said it.

Every request carries ``agent_id``, a timestamp, a random nonce and an HMAC over those and the body, keyed by a secret only the two of
them hold; the secret itself is never sent. A job the control plane hands out carries its own HMAC over the instruction, so an
instruction altered on the way (or one not meant for this pool) is refused before anything runs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any

#: The only operations an agent performs. Anything else is refused, whoever sent it.
ALLOWED = (
    "project.create",
    "project.update",
    "project.bind_database",
    "project.delete",
    "environment.create",
    "project.status",
)
#: How far a timestamp may be from now, in seconds.
SKEW = 120


class ProtocolError(Exception):
    """A message that must not be acted on."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _mac(secret: str, text: str) -> str:
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()


def sign_request(
    secret: str, agent_id: str, body: Mapping[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    """The envelope the agent sends: the body plus identity, time, nonce and signature."""
    ts = int(now if now is not None else time.time())
    nonce = secrets.token_hex(12)
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    return {
        "agent_id": agent_id,
        "ts": ts,
        "nonce": nonce,
        "body": dict(body),
        "sig": _mac(secret, f"{agent_id}.{ts}.{nonce}.{digest}"),
    }


def verify_request(
    secret: str, envelope: Mapping[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    """The body of a request whose signature and time hold. Raises :class:`ProtocolError`."""
    try:
        agent_id, ts, nonce, body, sig = (
            str(envelope["agent_id"]),
            int(envelope["ts"]),
            str(envelope["nonce"]),
            envelope["body"],
            str(envelope["sig"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("the message is malformed") from exc
    if abs((now if now is not None else time.time()) - ts) > SKEW:
        raise ProtocolError("the message is too old or from the future")
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    if not hmac.compare_digest(sig, _mac(secret, f"{agent_id}.{ts}.{nonce}.{digest}")):
        raise ProtocolError("the signature does not match")
    return dict(body)


def sign_job(secret: str, job: Mapping[str, Any]) -> str:
    """The signature the control plane puts on an instruction."""
    parts = {
        k: job.get(k)
        for k in ("id", "kind", "pool", "project_id", "payload", "expires_at", "attempt")
    }
    return _mac(secret, "job." + canonical(parts))


def verify_job(
    secret: str, job: Mapping[str, Any], *, pool: str, now: float | None = None
) -> dict[str, Any]:
    """An instruction this agent may carry out, or :class:`ProtocolError`."""
    if job.get("kind") not in ALLOWED:
        raise ProtocolError(f"{job.get('kind')!r} is not an operation an agent performs")
    if job.get("pool") != pool:
        raise ProtocolError("the instruction is for another runtime pool")
    if not hmac.compare_digest(str(job.get("sig", "")), sign_job(secret, job)):
        raise ProtocolError("the instruction's signature does not match")
    expires = job.get("expires_at")
    if expires is not None and float(expires) < (now if now is not None else time.time()):
        raise ProtocolError("the instruction has expired")
    if not isinstance(job.get("id"), str) or not job["id"]:
        raise ProtocolError("the instruction has no id")
    return dict(job)
