"""Channels, publication, presence and fan-out: the realtime core.

``Realtime`` (in :mod:`pawabase_server.realtime.service`) is the entry point; the other modules are the pieces it is built from and can each be used and
tested on their own.
"""

from pawabase_server.realtime.connection import Connection
from pawabase_server.realtime.errors import CapacityError, PayloadTooLarge
from pawabase_server.realtime.fanout import FANOUT_CHANNEL, INSTANCE
from pawabase_server.realtime.identity import TokenInvalid
from pawabase_server.realtime.names import (
    ChannelNameError,
    check_channel,
    check_event,
    room_name,
    split_room,
)
from pawabase_server.realtime.rules import ChannelConfig, ChannelRule, build_config
from pawabase_server.realtime.service import Realtime

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
