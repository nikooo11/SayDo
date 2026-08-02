"""Local transcript history + usage stats. One JSONL file, newest last.

Lives in ~/Library/Application Support/VoiceBud/history.jsonl for both the
dev checkout and the frozen app, so upgrading never loses history.
"""
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_DIR = Path.home() / "Library" / "Application Support" / "VoiceBud"
_FILE = _DIR / "history.jsonl"


def append(text, duration_s):
    _DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "text": text,
        "words": len(text.split()),
        "duration_s": round(float(duration_s), 2),
    }
    with open(_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_all(limit=500):
    if not _FILE.exists():
        return []
    entries = []
    with open(_FILE) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries[-limit:]


def clear():
    if _FILE.exists():
        _FILE.unlink()


def stats():
    entries = read_all(limit=100000)
    total_words = sum(e["words"] for e in entries)
    total_minutes = sum(e.get("duration_s", 0) for e in entries) / 60
    wpm = round(total_words / total_minutes) if total_minutes > 0.05 else 0

    days_with_use = {date.fromtimestamp(e["ts"]) for e in entries}
    streak = 0
    day = date.today()
    if day not in days_with_use and (day - timedelta(days=1)) in days_with_use:
        day -= timedelta(days=1)  # today unused yet — streak counts through yesterday
    while day in days_with_use:
        streak += 1
        day -= timedelta(days=1)

    # words per day for the last 14 days, oldest first
    daily = []
    for i in range(13, -1, -1):
        d = date.today() - timedelta(days=i)
        words = sum(e["words"] for e in entries if date.fromtimestamp(e["ts"]) == d)
        daily.append({"day": d.strftime("%a"), "date": d.isoformat(), "words": words})

    return {
        "total_words": total_words,
        "sessions": len(entries),
        "wpm": wpm,
        "streak": streak,
        "daily": daily,
        "voice": _voice_insights(entries, wpm),
    }


_STOPWORDS = set(
    "the a an and or but so of to in on at for with is are was were be been it its "
    "this that these those i you he she we they me my your our their them him her "
    "as if then than just can could should would will do does did done have has had "
    "not no yes ok okay um uh like know going get got want need make sure also very "
    "really there here when what which who how why all any some more most other into "
    "about out up down over after before because from".split()
)


def _voice_insights(entries, wpm):
    words = []
    for e in entries:
        words.extend(w.strip(".,!?;:'\"()").lower() for w in e["text"].split())
    words = [w for w in words if w]

    content = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    top_word = None
    if content:
        freq = {}
        for w in content:
            freq[w] = freq.get(w, 0) + 1
        best = max(freq, key=freq.get)
        if freq[best] >= 3:
            top_word = {"word": best, "count": freq[best]}

    # catchphrase: most repeated 3-word phrase (falls back to 2-word)
    catchphrase = None
    for n in (3, 2):
        grams = {}
        for e in entries:
            ws = [w.strip(".,!?;:'\"()").lower() for w in e["text"].split()]
            for i in range(len(ws) - n + 1):
                g = " ".join(ws[i:i + n])
                grams[g] = grams.get(g, 0) + 1
        grams = {g: c for g, c in grams.items() if c >= 3
                 and not all(w in _STOPWORDS for w in g.split())}
        if grams:
            catchphrase = max(grams, key=grams.get)
            break

    peak = None
    if entries:
        buckets = {}
        for e in entries:
            dt = datetime.fromtimestamp(e["ts"])
            key = (dt.strftime("%A"), dt.hour)
            buckets[key] = buckets.get(key, 0) + 1
        (day, hour), _ = max(buckets.items(), key=lambda kv: kv[1])
        h12 = hour % 12 or 12
        peak = f"{day} at {h12} {'a.m.' if hour < 12 else 'p.m.'}"

    avg = (sum(e["words"] for e in entries) / len(entries)) if entries else 0
    hours = [datetime.fromtimestamp(e["ts"]).hour for e in entries]
    night = sum(1 for h in hours if h >= 21 or h < 6) > len(hours) / 2 if hours else False
    if not entries:
        profile, blurb = "Warming Up", "Dictate a few times and your voice profile appears here."
    elif avg >= 35:
        profile = "Night Narrator" if night else "Long-form Narrator"
        blurb = "You think in full paragraphs — long, complete thoughts in a single take."
    elif avg >= 15:
        profile = "Night Drafter" if night else "Steady Drafter"
        blurb = "You dictate in measured, sentence-sized passes and build text steadily."
    else:
        profile = "Rapid-fire Commander"
        blurb = "Short, decisive bursts — you use your voice like a command line."

    return {
        "profile": profile,
        "profile_blurb": blurb,
        "top_word": top_word,
        "catchphrase": catchphrase,
        "peak": peak,
        "avg_words": round(avg),
    }
