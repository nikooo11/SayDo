"""System-wide push-to-talk hotkeys via pynput.

Supports a single key ("alt_r") or a chord ("ctrl+alt"), and two modes:
  hold   — record while the keys are held, stop on release
  toggle — press the chord once to start, press it again to stop
Note: macOS does not expose the fn key to apps, so fn can't be used.

TIS/TSM crash rule (macOS 26 aborts on violation — EXC_CRASH in HIToolbox,
"TIS/TSM API is being called in two threads concurrently"): in a UI app the
Text Input Sources API may only be called on the MAIN thread, and AppKit
itself calls it there (menu shortcut updates). pynput's Listener thread
calls TISCopyCurrentKeyboardInputSource when building its keycode context,
which races AppKit and kills the app. Two defenses here, both required:
  1. _freeze_keycode_context() captures the keyboard-layout context ON THE
     MAIN THREAD once and patches pynput to reuse it, so listener threads
     never touch TIS. (Chords use modifier keys only, so a stale layout
     after a keyboard-layout switch is harmless.)
  2. All PushToTalk instances share ONE Listener; never create a second.
PushToTalk.start() must be called from the main thread.
"""
import contextlib

from pynput import keyboard
from pynput._util import darwin as _pynput_util_darwin
from pynput.keyboard import _darwin as _pynput_kb_darwin


def _freeze_keycode_context():
    """Run pynput's TIS-touching context setup on the calling (main) thread
    and make every future keycode_context() reuse the result."""
    with _pynput_util_darwin.keycode_context() as ctx:
        cached = ctx

    @contextlib.contextmanager
    def _cached_context():
        yield cached

    _pynput_util_darwin.keycode_context = _cached_context
    _pynput_kb_darwin.keycode_context = _cached_context

KEY_MAP = {
    "alt": keyboard.Key.alt,        # left Option
    "alt_r": keyboard.Key.alt_r,    # right Option
    "ctrl": keyboard.Key.ctrl,      # left Control
    "ctrl_r": keyboard.Key.ctrl_r,
    "cmd": keyboard.Key.cmd,
    "cmd_r": keyboard.Key.cmd_r,
    "shift": keyboard.Key.shift,
    "f13": keyboard.Key.f13,
}


class PushToTalk:
    _handlers = []
    _listener = None

    def __init__(self, key_name, on_press, on_release, mode="hold"):
        names = [n.strip() for n in key_name.split("+")]
        unknown = [n for n in names if n not in KEY_MAP]
        if unknown:
            raise ValueError(f"Unknown hotkey(s) {unknown}. Choose from: {list(KEY_MAP)}")
        self.chord = {KEY_MAP[n] for n in names}
        self.mode = mode
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self._down = set()
        self._chord_held = False   # chord physically complete right now
        self._recording = False    # logical recording state (drives toggle mode)

    def _press(self, key):
        if key not in self.chord:
            return
        self._down.add(key)
        if not self._chord_held and self._down == self.chord:
            self._chord_held = True
            if self.mode == "toggle":
                if self._recording:
                    self._recording = False
                    self.on_release_cb()
                else:
                    self._recording = True
                    self.on_press_cb()
            else:  # hold
                self._recording = True
                self.on_press_cb()

    def _release(self, key):
        if key not in self.chord:
            return
        self._down.discard(key)
        if self._chord_held and self._down != self.chord:
            self._chord_held = False
            if self.mode == "hold" and self._recording:
                self._recording = False
                self.on_release_cb()

    @classmethod
    def _dispatch_press(cls, key):
        for handler in list(cls._handlers):
            handler._press(key)

    @classmethod
    def _dispatch_release(cls, key):
        for handler in list(cls._handlers):
            handler._release(key)

    def start(self):
        """Register this chord and start the shared listener (non-blocking).
        Must be called from the main thread (see module docstring)."""
        cls = PushToTalk
        if self not in cls._handlers:
            cls._handlers.append(self)
        if cls._listener is None:
            _freeze_keycode_context()
            cls._listener = keyboard.Listener(
                on_press=cls._dispatch_press, on_release=cls._dispatch_release)
            cls._listener.start()

    def run(self):
        self.start()
        PushToTalk._listener.join()
