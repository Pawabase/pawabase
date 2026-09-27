"""The built-in blocks, registered through the ``pawabase.blocks`` entry point."""

from __future__ import annotations

from ..registry import BlockRegistry
from . import control, data, platform, triggers

ALL_BLOCKS = [*triggers.BLOCKS, *control.BLOCKS, *data.BLOCKS, *platform.BLOCKS]


def register(registry: BlockRegistry) -> None:
    """Add every built-in block to *registry*."""
    for block in ALL_BLOCKS:
        registry.add(block)
