"""Channel and event names, and the room a channel lives in.

A room is ``<environment>/<channel>``, so two environments can use the same channel name and never share traffic. That only holds if a
channel name can never contain the separator, nor a glob character that would let a rule or a subscription reach beyond its own name,
so names are held to a small alphabet.
"""

from __future__ import annotations

import re

CHANNEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.@=+\-]{0,199}$")
EVENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.\-]{0,99}$")
MAX_CHANNEL = 200


class ChannelNameError(PermissionError):
    """A channel or event name that is not allowed. A :class:`PermissionError`, so callers treat it as a refusal."""


def room_name(env: str, channel: str) -> str:
    return f"{env}/{channel}"


def split_room(room: str) -> tuple[str, str]:
    env, channel = room.split("/", 1)
    return env, channel


def check_channel(channel: object) -> str:
    """The channel name, or :class:`ChannelNameError`."""
    if not isinstance(channel, str) or not channel:
        raise ChannelNameError("invalid channel name")
    if not CHANNEL.match(channel):
        raise ChannelNameError("invalid channel name")
    return channel


def check_event(event: object, default: str = "message") -> str:
    """The event name (``default`` when none is given), or :class:`ChannelNameError`."""
    if event is None or event == "":
        return default
    if not isinstance(event, str) or not EVENT.match(event):
        raise ChannelNameError("invalid event name")
    return event
