"""Blocks and how they are found.

A block is a class with a ``key``, a description of its configuration (so
Studio can render a form for it), its output handles, and an async ``run``.
Blocks are collected from the ``pawabase.blocks`` entry-point group, so an
installed package can add blocks without touching Pawabase::

    # pyproject.toml of an extension
    [project.entry-points."pawabase.blocks"]
    stripe = "pawabase_stripe.blocks:register"

    # pawabase_stripe/blocks.py
    def register(registry):
        registry.add(CreateCharge)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .engine import FlowRun

logger = logging.getLogger("pawabase.flows")

ENTRY_POINT_GROUP = "pawabase.blocks"


@dataclass(slots=True)
class BlockResult:
    """What a block returns.

    Attributes:
        output: Stored as ``steps.<node id>.output`` for later blocks.
        handle: Which outgoing edges to follow (``next``, ``true``, ``false``,
            a switch case…).
        stop: End the run after this block.
    """

    output: Any = None
    handle: str = "next"
    stop: bool = False


class Block:
    """Base class for blocks.

    Class attributes describe the block to Studio; ``run`` does the work.
    ``config`` arrives with templates already rendered, unless the block sets
    ``raw_config`` and renders what it needs itself (conditions and loops do).
    """

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    category: ClassVar[str] = "general"
    description: ClassVar[str] = ""
    #: Configuration fields, in the schema format of :mod:`pawabase_core.schemas`,
    #: plus an optional ``widget`` hint for Studio.
    config: ClassVar[list[dict[str, Any]]] = []
    #: Output handles, in display order.
    handles: ClassVar[list[str]] = ["next"]
    #: Leave ``{{ }}`` templates in the configuration for the block to render.
    raw_config: ClassVar[bool] = False
    #: Whether this block starts a run.
    trigger: ClassVar[bool] = False

    async def run(self, config: dict[str, Any], run: FlowRun) -> BlockResult:
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The block's catalogue entry, for Studio."""
        return {
            "key": cls.key,
            "title": cls.title or cls.key,
            "category": cls.category,
            "description": cls.description or (cls.__doc__ or "").strip().split("\n")[0],
            "config": cls.config,
            "handles": cls.handles,
            "trigger": cls.trigger,
        }


@dataclass
class BlockRegistry:
    """Every block this process knows, by key."""

    blocks: dict[str, type[Block]] = field(default_factory=dict)

    def add(self, block: type[Block]) -> type[Block]:
        if not block.key:
            raise ValueError(f"{block.__name__} has no key")
        self.blocks[block.key] = block
        return block

    def get(self, key: str) -> type[Block]:
        try:
            return self.blocks[key]
        except KeyError:
            raise KeyError(f"unknown block {key!r}") from None

    def catalogue(self) -> list[dict[str, Any]]:
        return [
            block.describe()
            for block in sorted(self.blocks.values(), key=lambda b: (b.category, b.key))
        ]

    def __contains__(self, key: object) -> bool:
        return key in self.blocks

    def __len__(self) -> int:
        return len(self.blocks)


_default: BlockRegistry | None = None


def default_registry() -> BlockRegistry:
    """The process-wide registry, filled from entry points on first use."""
    global _default
    if _default is None:
        registry = BlockRegistry()
        from .blocks import register as register_builtins

        register_builtins(registry)
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            if entry.value.startswith("pawabase_core.flows.blocks"):
                continue
            try:
                entry.load()(registry)
            except Exception:
                logger.exception("could not load blocks from %s", entry.value)
        _default = registry
    return _default


def describe_config(block: type[Block]) -> Mapping[str, Any]:
    """The block's configuration fields by name."""
    return {spec["name"]: spec for spec in block.config}
