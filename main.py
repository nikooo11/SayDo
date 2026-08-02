"""VoiceBud: hold the hotkey to dictate anywhere. Fully offline.

Setup (5 lines):
  python3.12 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  Grant Terminal: Microphone, Accessibility, Input Monitoring (System Settings -> Privacy & Security)
  ollama serve   (in another terminal, optional — transcript cleanup)
  python main.py
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

import dictionary
import history
import inject
import launch_login
import snippets
from audio import Recorder
from cleanup import Cleaner
from hotkey import PushToTalk
from transcribe import Transcriber

APP_NAME = "VoiceBud"

UI_DEFAULTS = {"flow_bar": True, "sounds": True, "mute_music": False}

# settings that need a full engine restart to apply
RESTART_KEYS = {"hotkey", "stt", "audio"}


def check_permissions():
    """Best-effort permission probes; print one-time setup guidance if missing."""
    import Quartz
    msgs = []
    try:
        if not Quartz.CGPreflightListenEventAccess():
            msgs.append("Input Monitoring (for the global hotkey)")
    except AttributeError:
        pass
    try:
        from ApplicationServices import AXIsProcessTrusted
        if not AXIsProcessTrusted():
            msgs.append("Accessibility (to paste text into other apps)")
    except Exception:
        pass
    if msgs:
        print("SETUP NEEDED — grant your terminal these permissions in")
        print("System Settings -> Privacy & Security, then restart this app:")
        for m in msgs:
            print(f"  - {m}")
        print("  - Microphone (macOS will prompt on first recording)")


def rename_app():
    """Best-effort: show 'VoiceBud' instead of 'Python' where macOS reads the bundle name."""
    try:
        from Foundation import NSBundle, NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_(APP_NAME)
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
    except Exception:
        pass


def resolve_input_device(name):
    """Config device name -> sounddevice index; None = system default."""
    if not name:
        return None
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["name"] == name:
            return i
    print(f'WARNING: input device "{name}" not found; using system default')
    return None


_MUSIC_APPS = ("Music", "Spotify")


class MusicDucker:
    """Pause Music/Spotify while dictating, resume what we paused. Best-effort."""

    def __init__(self):
        self._paused = []

    def _osa(self, script):
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, timeout=3, text=True)
            return out.stdout.strip()
        except Exception:
            return ""

    def pause(self):
        self._paused = []
        for app in _MUSIC_APPS:
            playing = self._osa(
                f'tell application "System Events" to (name of processes) contains "{app}"'
            ) == "true" and self._osa(
                f'tell application "{app}" to player state as string') == "playing"
            if playing:
                self._osa(f'tell application "{app}" to pause')
                self._paused.append(app)

    def resume(self):
        for app in self._paused:
            self._osa(f'tell application "{app}" to play')
        self._paused = []


def main():
    from bundle import config_path
    cfg_path = config_path()
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    cfg["ui"] = {**UI_DEFAULTS, **(cfg.get("ui") or {})}

    check_permissions()

    from AppKit import NSApplication, NSMenu, NSMenuItem, NSSound, NSStatusBar
    from PyObjCTools import AppHelper
    from overlay import Overlay
    from window import Dashboard, MenuTarget, write_config

    rename_app()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # accessory: no Dock icon

    print(f"Loading STT model ({cfg['stt']['model']})...")
    stt = Transcriber(cfg["stt"])
    state = {"cleaner": Cleaner(cfg["llm"])}
    rec = Recorder(
        sample_rate=cfg["audio"]["sample_rate"],
        channels=cfg["audio"]["channels"],
        preroll_ms=cfg["audio"]["preroll_ms"],
        device=resolve_input_device(cfg["audio"].get("device")),
    )
    rec.start_stream()

    key = cfg["hotkey"]["key"]
    mode = cfg["hotkey"].get("mode", "hold")
    pretty_key = " + ".join(p.strip() for p in key.split("+"))
    hint = f"{'Hold' if mode == 'hold' else 'Press'} {pretty_key} to dictate"
    overlay = Overlay(lambda: rec.level, hint=hint, flow_bar=cfg["ui"]["flow_bar"])
    ducker = MusicDucker()

    def play_sound(name):
        if cfg["ui"].get("sounds"):
            s = NSSound.soundNamed_(name)
            if s is not None:
                s.play()

    def restart_app():
        rec.close()
        os.execv(sys.executable, [sys.executable] if getattr(sys, "frozen", False)
                 else [sys.executable, "-u", str(Path(__file__).resolve().parent / "main.py")])

    class Controller:
        @staticmethod
        def status():
            return {"recording": rec._recording, "level": rec.level,
                    "cleanup": state["cleaner"].backend or "off",
                    "model": cfg["stt"]["model"], "hotkey": key, "mode": mode}

        @staticmethod
        def save_settings(partial):
            needs_restart = any(
                k in RESTART_KEYS and partial[k] != cfg.get(k) for k in partial)
            for k, v in partial.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, dict) and isinstance(cfg[k].get(k2), dict):
                            cfg[k][k2].update(v2)
                        else:
                            cfg[k][k2] = v2
                else:
                    cfg[k] = v
            write_config(cfg, cfg_path)
            # live-apply what we can
            if "llm" in partial:
                state["cleaner"] = Cleaner(cfg["llm"])
            if "ui" in partial:
                if "flow_bar" in partial["ui"]:
                    AppHelper.callAfter(overlay.set_flow_bar, cfg["ui"]["flow_bar"])
                if "launch_login" in partial["ui"]:
                    launch_login.set_enabled(partial["ui"]["launch_login"])
            if needs_restart:
                threading.Timer(0.6, lambda: AppHelper.callAfter(restart_app)).start()
                return {"ok": True, "restarting": True}
            return {"ok": True, "restarting": False}

        @staticmethod
        def restart():
            threading.Timer(0.3, lambda: AppHelper.callAfter(restart_app)).start()

    Controller.cfg = cfg
    dashboard = Dashboard(Controller)
    # debug/automation hook: open the dashboard at launch if the flag file exists
    _flag = Path.home() / "Library" / "Application Support" / "VoiceBud" / ".open-dashboard"
    if _flag.exists():
        _flag.unlink(missing_ok=True)
        AppHelper.callAfter(dashboard.open)
    menu_target = MenuTarget.alloc().initWithDashboard_(dashboard)

    # menu bar icon so VoiceBud is visible/controllable like a normal app
    status = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
    status.button().setTitle_("🎙️")
    menu = NSMenu.alloc().init()
    open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open VoiceBud", "openDashboard:", "o")
    open_item.setTarget_(menu_target)
    menu.addItem_(open_item)
    hint_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"{hint} ({mode} mode)", None, "")
    hint_item.setEnabled_(False)
    menu.addItem_(hint_item)
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit " + APP_NAME, "terminate:", "q")
    menu.addItem_(quit_item)
    status.setMenu_(menu)

    def on_press():
        if cfg["ui"].get("mute_music"):
            threading.Thread(target=ducker.pause, daemon=True).start()
        play_sound("Pop")
        rec.start()
        AppHelper.callAfter(overlay.show)

    def process(audio):
        t0 = time.time()
        raw = stt.transcribe(audio)
        if not raw:
            print("(no speech detected)")
            return
        text = dictionary.apply_corrections(raw)
        text = state["cleaner"].clean(text)
        text = snippets.apply(text)
        inject.inject(text, cfg["inject"])
        history.append(text, audio.size / cfg["audio"]["sample_rate"])
        print(f'→ "{text}"  ({time.time() - t0:.2f}s)')

    def on_release():
        audio = rec.stop()
        play_sound("Bottle")
        if cfg["ui"].get("mute_music"):
            threading.Thread(target=ducker.resume, daemon=True).start()
        AppHelper.callAfter(overlay.hide)
        threading.Thread(target=process, args=(audio,), daemon=True).start()

    PushToTalk(key, on_press, on_release, mode=mode).start()
    action = "Press" if mode == "toggle" else "Hold"
    print(f"{APP_NAME} ready. {action} [{key}] to dictate ({mode} mode).")
    try:
        AppHelper.runEventLoop()
    finally:
        rec.close()


if __name__ == "__main__":
    main()
