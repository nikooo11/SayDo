"""Path helpers so the same code runs from a checkout or a frozen PyInstaller .app."""
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def config_path():
    """User-editable config location.

    Frozen app: ~/Library/Application Support/SayDo/config.yaml, seeded from
    the bundled default on first run so users can edit it without touching the .app.
    Dev checkout: config.yaml next to the code, unchanged behavior.
    """
    if not FROZEN:
        return Path(__file__).parent / "config.yaml"
    support = Path.home() / "Library" / "Application Support" / "SayDo"
    support.mkdir(parents=True, exist_ok=True)
    cfg = support / "config.yaml"
    if not cfg.exists():
        shutil.copy(resource_dir() / "config.yaml", cfg)
    return cfg


def bundled_model(name):
    """Path to a Whisper model shipped inside the app, or None to download as usual."""
    d = resource_dir() / "models" / f"faster-whisper-{name}"
    return str(d) if (d / "model.bin").exists() else None
