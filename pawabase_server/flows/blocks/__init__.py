"""The built-in blocks, registered through the ``pawabase.blocks`` entry point."""

from __future__ import annotations

from pawabase_server.flows.blocks import control, data, platform, triggers
from pawabase_server.flows.registry import BlockRegistry

ALL_BLOCKS = [*triggers.BLOCKS, *control.BLOCKS, *data.BLOCKS, *platform.BLOCKS]


def register(registry: BlockRegistry) -> None:
    """Add every built-in block to *registry*."""
    for block in ALL_BLOCKS:
        registry.add(block)
