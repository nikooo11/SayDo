"""Text injection at the cursor: clipboard + synthesized Cmd+V (Quartz CGEvent),
with a per-character Unicode keystroke fallback. Saves/restores the clipboard."""
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

KEY_V = 9  # macOS virtual keycode for 'v'


def _set_clipboard(text):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def _snapshot_clipboard():
    """Capture every item on the clipboard with all its types (text, images,
    files, rich text), so restore is byte-for-byte."""
    pb = NSPasteboard.generalPasteboard()
    items = []
    for item in pb.pasteboardItems() or []:
        entry = [(t, item.dataForType_(t)) for t in item.types()]
        entry = [(t, d) for t, d in entry if d is not None]
        if entry:
            items.append(entry)
    return items


def _restore_clipboard(items):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    if not items:
        return  # clipboard was empty before — leave it empty
    objs = []
    for entry in items:
        pi = NSPasteboardItem.alloc().init()
        for t, d in entry:
            pi.setData_forType_(d, t)
        objs.append(pi)
    pb.writeObjects_(objs)


def _press_cmd_v():
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(src, KEY_V, True)
    up = Quartz.CGEventCreateKeyboardEvent(src, KEY_V, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def _type_unicode(text):
    """Fallback: per-character CGEvent Unicode keystrokes (no clipboard involved)."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for ch in text:
        down = Quartz.CGEventCreateKeyboardEvent(src, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(down, len(ch), ch)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        up = Quartz.CGEventCreateKeyboardEvent(src, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(ch), ch)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.002)


def inject(text, cfg):
    if not text:
        return
    if cfg.get("method", "paste") == "type":
        _type_unicode(text)
        return
    restore = cfg.get("restore_clipboard", True)
    old = _snapshot_clipboard() if restore else None
    _set_clipboard(text)
    time.sleep(0.05)  # let the pasteboard settle
    _press_cmd_v()
    if restore:
        time.sleep(0.25)  # let the paste land before restoring
        _restore_clipboard(old)
