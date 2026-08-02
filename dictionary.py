"""Custom vocabulary and misspelling corrections.

Words bias Whisper toward your jargon (fed in as the model's initial prompt)
and are protected during LLM cleanup. Misspelling entries are applied as
post-transcription text corrections. Stored as JSON in Application Support.
"""
import json
import re
import time
from pathlib import Path

_DIR = Path.home() / "Library" / "Application Support" / "VoiceBud"
_FILE = _DIR / "dictionary.json"

_cache = {"mtime": None, "entries": []}


def _load():
    try:
        mtime = _FILE.stat().st_mtime
    except FileNotFoundError:
        _cache.update(mtime=None, entries=[])
        return _cache["entries"]
    if mtime != _cache["mtime"]:
        try:
            _cache["entries"] = json.loads(_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            _cache["entries"] = []
        _cache["mtime"] = mtime
    return _cache["entries"]


def _save(entries):
    _DIR.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(entries, indent=2))
    _cache.update(mtime=None)


def entries():
    return list(_load())


def add(word, misspelling=None):
    word = word.strip()
    if not word:
        return
    items = [e for e in _load() if e["word"].lower() != word.lower()]
    items.append({
        "word": word,
        "misspelling": (misspelling or "").strip() or None,
        "hits": 0,
        "added": time.time(),
    })
    _save(items)


def remove(word):
    _save([e for e in _load() if e["word"].lower() != word.lower()])


def words():
    """Vocabulary list for Whisper's initial prompt (correct spellings only)."""
    return [e["word"] for e in _load()]


def apply_corrections(text):
    """Replace known misspellings with their corrections, whole-word,
    case-insensitive. Counts hits so Insights can show 'most corrected'."""
    items = _load()
    changed = False
    for e in items:
        if not e.get("misspelling"):
            continue
        pattern = re.compile(r"\b" + re.escape(e["misspelling"]) + r"\b", re.IGNORECASE)
        text, n = pattern.subn(e["word"], text)
        if n:
            e["hits"] = e.get("hits", 0) + n
            changed = True
    if changed:
        _save(items)
    return text


def most_corrected():
    items = [e for e in _load() if e.get("hits", 0) > 0]
    if not items:
        return None
    top = max(items, key=lambda e: e["hits"])
    return {"word": top["word"], "hits": top["hits"]}
