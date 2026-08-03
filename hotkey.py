"""System-wide push-to-talk hotkeys via pynput.

Supports a single key ("alt_r") or a chord ("ctrl+alt"), and two modes:
  hold   — record while the keys are held, stop on release
  toggle — press the chord once to start, press it again to stop
Note: macOS does not expose the fn key to apps, so fn can't be used.

All PushToTalk instances share ONE pynput Listener. Each Listener runs its
own thread and calls the Text Input Sources API (TISCopyCurrentKeyboardInput-
Source) on startup; macOS aborts the process when TIS is entered from two
threads concurrently, so starting a second Listener (e.g. the rewrite chord
next to the dictation chord) crashes the app at boot. Never create more than
one Listener in this process.
"""
from pynput import keyboard

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
        """Register this chord and start the shared listener (non-blocking)."""
        cls = PushToTalk
        if self not in cls._handlers:
            cls._handlers.append(self)
        if cls._listener is None:
            cls._listener = keyboard.Listener(
                on_press=cls._dispatch_press, on_release=cls._dispatch_release)
            cls._listener.start()

    def run(self):
        self.start()
        PushToTalk._listener.join()
