"""Spoken snippets: say a trigger phrase, get the saved expansion typed instead.

A snippet fires when the whole utterance is (close to) the trigger, or the
trigger appears inside a longer utterance — 'my email address' anywhere in a
sentence becomes the saved address. Stored as JSON in Application Support.
"""
import json
import re
import time
from pathlib import Path

_DIR = Path.home() / "Library" / "Application Support" / "VoiceBud"
_FILE = _DIR / "snippets.json"


def _load():
    try:
        return json.loads(_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(items):
    _DIR.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(items, indent=2))


def entries():
    return _load()


def add(trigger, expansion):
    trigger = trigger.strip()
    if not trigger or not expansion:
        return
    items = [s for s in _load() if s["trigger"].lower() != trigger.lower()]
    items.append({"trigger": trigger, "expansion": expansion,
                  "hits": 0, "added": time.time()})
    _save(items)


def remove(trigger):
    _save([s for s in _load() if s["trigger"].lower() != trigger.lower()])


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def apply(text):
    """Expand snippet triggers in a transcript. Whole-utterance match first,
    then inline phrase replacement. Counts hits for Insights."""
    items = _load()
    changed = False
    for s in items:
        if _norm(text) == _norm(s["trigger"]):
            s["hits"] = s.get("hits", 0) + 1
            _save(items)
            return s["expansion"]
    for s in items:
        pattern = re.compile(re.escape(s["trigger"]), re.IGNORECASE)
        text, n = pattern.subn(lambda _m: s["expansion"], text)
        if n:
            s["hits"] = s.get("hits", 0) + n
            changed = True
    if changed:
        _save(items)
    return text
