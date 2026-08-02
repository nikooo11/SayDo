"""Launch-at-login via a per-user LaunchAgent."""
import plistlib
import subprocess
import sys
from pathlib import Path

_LABEL = "com.nikooo11.voicebud"
_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _launch_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    app = Path("/Applications/VoiceBud.app")
    if app.exists():
        return ["/usr/bin/open", "-a", str(app)]
    return [sys.executable, str(Path(__file__).parent / "main.py")]


def enabled():
    return _PLIST.exists()


def set_enabled(on):
    if on:
        _PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(_PLIST, "wb") as f:
            plistlib.dump({
                "Label": _LABEL,
                "ProgramArguments": _launch_command(),
                "RunAtLoad": True,
            }, f)
        subprocess.run(["launchctl", "load", str(_PLIST)], capture_output=True)
    elif _PLIST.exists():
        subprocess.run(["launchctl", "unload", str(_PLIST)], capture_output=True)
        _PLIST.unlink(missing_ok=True)
