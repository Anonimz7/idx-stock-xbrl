"""Local Firefox browser bridge."""

from .client import FirefoxBridgeClient
from .config import Settings, get_settings

__all__ = ["FirefoxBridgeClient", "Settings", "get_settings"]
__version__ = "0.1.0"
