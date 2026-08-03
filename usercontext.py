"""Speaker context: a short free-text note about the user (names, products,
jargon) that the cleanup LLM reads so it can fix terms the ear got wrong.
Text only ever goes to the cleanup backend the user already chose."""
from pathlib import Path

_FILE = Path.home() / "Library" / "Application Support" / "SayDo" / "context.txt"
_MAX_CHARS = 2000


def text():
    try:
        return _FILE.read_text().strip()[:_MAX_CHARS]
    except OSError:
        return ""


def save(value):
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text((value or "").strip()[:_MAX_CHARS])
