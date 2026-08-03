"""Custom vocabulary and misspelling corrections.

Words bias Whisper toward your jargon (fed in as the model's initial prompt)
and are protected during LLM cleanup. Parakeet takes no initial prompt, so
apply_fuzzy() additionally snaps near-miss tokens onto dictionary words after
transcription. Misspelling entries are applied as exact text corrections.
Stored as JSON in Application Support.
"""
import difflib
import json
import re
import time
from pathlib import Path

_DIR = Path.home() / "Library" / "Application Support" / "SayDo"
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


_TOKEN = re.compile(r"^(\W*)([\w'-]+)(\W*)$", re.UNICODE)
_SUFFIXES = ("s", "es", "d", "ed", "ing", "'s", "s'")


def _core(token):
    m = _TOKEN.match(token)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", "", token)


def _norm(s):
    return re.sub(r"[\W_]", "", s).lower()


def _is_inflection(a, b):
    """True when one string is just the other plus a common suffix
    (stripe/stripes, grade/graded): those are different words, not typos."""
    for shorter, longer in ((a, b), (b, a)):
        if any(longer == shorter + s for s in _SUFFIXES):
            return True
    return False


def _styled(word, token_visible):
    """Dictionary casing wins, except a plain-lowercase dictionary word keeps
    the token's sentence-start capital."""
    if word != word.lower():
        return word
    return word.capitalize() if token_visible[:1].isupper() else word


def apply_fuzzy(text):
    """Snap near-miss transcript tokens onto dictionary words. Conservative:
    fuzzy only for words of 5+ chars, same first letter, similarity >= 0.86,
    never across inflections. Also joins a split pair ('supa base') when the
    concatenation matches a dictionary word exactly."""
    items = _load()
    if not items or not text:
        return text
    norm_words = [(e, _norm(e["word"])) for e in items if _norm(e["word"])]
    if not norm_words:
        return text
    tokens = text.split(" ")
    out, changed, i = [], False, 0
    while i < len(tokens):
        lead, core, trail = _core(tokens[i])
        low = core.lower()
        hit = None
        exact = next((e for e, nw in norm_words if low and nw == low), None)
        if exact is not None:
            word = exact["word"]
            # snap to distinctive casing (SayDo, iOS); plain words keep theirs
            if word[1:] != word[1:].lower() and core != word:
                out.append(lead + word + trail)
                hit = exact
                i += 1
        elif low:
            # exact match after joining 2-3 tokens ('supa base', 'air line prep').
            # min lengths keep short real phrases ('say do it') from being eaten;
            # add a misspelling entry to force short joins explicitly.
            for span, min_len in ((2, 6), (3, 10)):
                parts = [_core(t) for t in tokens[i:i + span]]
                if len(parts) < span or any(not c or l for l, c, _ in parts[1:]):
                    continue
                joined = low + "".join(c.lower() for _, c, _ in parts[1:])
                for e, nw in norm_words:
                    if len(nw) >= min_len and joined == nw:
                        out.append(lead + _styled(e["word"], core) + parts[-1][2])
                        hit = e
                        i += span
                        break
                if hit is not None:
                    break
            if hit is None and len(low) >= 4:
                for e, nw in norm_words:
                    if (len(nw) >= 5 and low[0] == nw[0]
                            and abs(len(low) - len(nw)) <= 2
                            and not _is_inflection(low, nw)
                            and difflib.SequenceMatcher(None, low, nw).ratio() >= 0.86):
                        out.append(lead + _styled(e["word"], core) + trail)
                        hit = e
                        i += 1
                        break
        if hit is None:
            out.append(tokens[i])
            i += 1
        else:
            hit["hits"] = hit.get("hits", 0) + 1
            changed = True
    if changed:
        _save(items)
    return " ".join(out)


def most_corrected():
    items = [e for e in _load() if e.get("hits", 0) > 0]
    if not items:
        return None
    top = max(items, key=lambda e: e["hits"])
    return {"word": top["word"], "hits": top["hits"]}
