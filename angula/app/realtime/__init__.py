"""Channels, publication, presence and fan-out: Angula's realtime core.

``Realtime`` (in :mod:`app.realtime.service`) is the entry point; the other modules are the pieces it is built from and can each be used and
tested on their own.
"""

from app.realtime.connection import Connection
from app.realtime.errors import CapacityError, PayloadTooLarge
from app.realtime.fanout import FANOUT_CHANNEL, INSTANCE
from app.realtime.identity import TokenInvalid
from app.realtime.names import ChannelNameError, check_channel, check_event, room_name, split_room
from app.realtime.rules import ChannelConfig, ChannelRule, build_config
from app.realtime.service import Realtime

__all__ = [
    "FANOUT_CHANNEL",
    "INSTANCE",
    "CapacityError",
    "ChannelConfig",
    "ChannelNameError",
    "ChannelRule",
    "Connection",
    "PayloadTooLarge",
    "Realtime",
    "TokenInvalid",
    "build_config",
    "check_channel",
    "check_event",
    "room_name",
    "split_room",
]
