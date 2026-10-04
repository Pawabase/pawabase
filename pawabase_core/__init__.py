"""pawabase-kit: shared building blocks for Pawabase services, built on Sillo."""

from .context import PlatformContext, current_context, require_context
from .settings import PlatformSettings

__version__ = "0.1.0"

__all__ = [
    "PlatformContext",
    "PlatformSettings",
    "__version__",
    "current_context",
    "require_context",
]
