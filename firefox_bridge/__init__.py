"""Local Firefox browser bridge."""

from importlib.metadata import PackageNotFoundError, version

from .client import FirefoxBridgeClient
from .config import Settings, get_settings

__all__ = ["FirefoxBridgeClient", "Settings", "get_settings"]


def _installed_version() -> str:
    """Read the version from the installed distribution, not from a second copy.

    A literal here is a second source of truth, and the two drift: this file said
    `0.1.0` while the metadata said `0.2.0`, so anything reporting the package
    version from here was wrong without a single test failing.

    Falls back to `unknown` when the package is not installed, which is the
    honest answer rather than a plausible-looking number.
    """
    try:
        return version("firefox-bridge")
    except PackageNotFoundError:  # pragma: no cover - only when run from a bare tree
        return "unknown"


__version__ = _installed_version()
