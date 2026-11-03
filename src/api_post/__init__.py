"""ReqDock package bootstrap, shared by desktop, service and CLI entry points."""

import platform
import sys

# CPython 3.12+ prefers synchronous WMI for Windows version/CPU detection.
# A stalled WMI provider can block keyring (and thus startup) indefinitely.
# Disable only that optional platform probe: CPython then uses its existing
# sys.getwindowsversion/registry/environment fallbacks. Never change WMI,
# credential storage, TLS settings or the machine-wide Python installation.
if sys.platform == "win32" and hasattr(platform, "_wmi"):
    platform._wmi = None

__version__ = "0.1.0"
