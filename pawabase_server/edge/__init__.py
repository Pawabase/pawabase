"""The public entry point: the checks every request from outside passes, each testable on its own.

``pipeline`` is the ASGI middleware that applies them in order; the rest are the pieces it is built from.
"""

from pawabase_server.edge.pipeline import Edge

__all__ = ["Edge"]
