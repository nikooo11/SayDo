"""Scratchpad: quick local notes. JSON store in Application Support."""
import json
import time
import uuid
from pathlib import Path

_DIR = Path.home() / "Library" / "Application Support" / "SayDo"
_FILE = _DIR / "notes.json"


def _load():
    try:
        return json.loads(_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(notes):
    _DIR.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(notes, indent=2))


def entries():
    return sorted(_load(), key=lambda n: n.get("updated", 0), reverse=True)


def save(note_id, title, body):
    notes = _load()
    for n in notes:
        if n["id"] == note_id:
            n.update(title=title, body=body, updated=time.time())
            break
    else:
        note_id = note_id or str(uuid.uuid4())
        notes.append({"id": note_id, "title": title, "body": body,
                      "created": time.time(), "updated": time.time()})
    _save(notes)
    return note_id


def delete(note_id):
    _save([n for n in _load() if n["id"] != note_id])
