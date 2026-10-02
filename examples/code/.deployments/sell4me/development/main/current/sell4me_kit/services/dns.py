"""Shared DNS helpers.

One function lives here today: a TXT-record lookup used by both the
storefront ``Domain`` verification and the POS device-domain verification.
Keeping it in one place means the two surfaces cannot drift apart in what
"verified" means, and a deployment that wants a real resolver (instead of
shelling out to ``dig``) replaces this one module.
"""

from __future__ import annotations

__all__ = ["lookup_txt"]


async def lookup_txt(name: str) -> list[str] | None:
    """TXT records for a name, or `None` if the lookup could not be made.

    `None` and `[]` mean different things and the caller depends on it: an
    empty list is "DNS answered, the record is not there"; `None` is "we could
    not ask", which must not fail a verification the merchant did correctly.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        import socket

        # No dnspython dependency: the stdlib cannot query TXT, so this shells
        # out to the platform resolver. A deployment that wants a real resolver
        # replaces this one function.
        process = await asyncio.create_subprocess_exec(
            "dig", "+short", "TXT", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        _ = loop, socket
    except (TimeoutError, FileNotFoundError, OSError):
        return None

    if process.returncode != 0:
        return None
    return [line.strip().strip('"') for line in stdout.decode().splitlines() if line.strip()]