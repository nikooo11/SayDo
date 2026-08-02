"""Per-app dictation modes: pick a pipeline mode from the frontmost app.

Rules live in config.yaml under app_modes.rules as
{match: "substring", mode: "standard" | "code" | "raw"}. A rule matches when
its `match` is a case-insensitive substring of the frontmost app's name; the
first matching rule wins. Modes:
  standard — dictionary corrections + LLM cleanup + snippets (default)
  code     — corrections + snippets, no LLM cleanup, trailing period dropped
  raw      — corrections only
"""

MODES = ("standard", "code", "raw")


def frontmost_app_name():
    """Localized name of the frontmost app, or None. Best-effort."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        name = app.localizedName() if app is not None else None
        return str(name) if name else None
    except Exception:
        return None


def resolve_mode(app_name, rules):
    """First matching rule's mode, else 'standard'. Tolerates junk rules."""
    if not app_name:
        return "standard"
    low = app_name.lower()
    for r in rules or []:
        if not isinstance(r, dict):
            continue
        m = str(r.get("match") or "").strip().lower()
        if m and m in low and r.get("mode") in MODES:
            return r["mode"]
    return "standard"


def code_postprocess(text):
    """Drop a lone trailing period on single-sentence utterances.

    Code commands rarely want one; anything multi-sentence is left alone.
    """
    t = text.rstrip()
    if t.endswith(".") and not t.endswith(".."):
        body = t[:-1]
        if not any(ch in body for ch in ".!?"):
            return body
    return text
