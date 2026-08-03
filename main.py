"""SayDo: hold the hotkey to dictate anywhere. Fully offline.

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

import appmodes
import dictionary
import history
import inject
import launch_login
import snippets
from audio import Recorder
from cleanup import Cleaner
from hotkey import PushToTalk
from transcribe import Transcriber

APP_NAME = "SayDo"

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
    """Best-effort: show 'SayDo' instead of 'Python' where macOS reads the bundle name."""
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


def migrate_data_dir():
    """One-time: carry VoiceBud-era history/dictionary/notes over to SayDo."""
    old = Path.home() / "Library" / "Application Support" / "VoiceBud"
    new = Path.home() / "Library" / "Application Support" / "SayDo"
    if old.exists() and not new.exists():
        import shutil
        shutil.move(str(old), str(new))


def main():
    migrate_data_dir()
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

    from window import permissions_status
    print(f"permissions: {permissions_status()}")
    print(f"Loading STT model ({cfg['stt']['model']})...")
    stt = Transcriber(cfg["stt"])
    state = {"cleaner": Cleaner(cfg["llm"]), "front_app": None}
    rec = Recorder(
        sample_rate=cfg["audio"]["sample_rate"],
        channels=cfg["audio"]["channels"],
        preroll_ms=cfg["audio"]["preroll_ms"],
    )

    def _current_input_name():
        import sounddevice as sd
        try:
            return sd.query_devices(sd.default.device[0])["name"]
        except Exception:
            return "unknown"

    print(f"default mic: {_current_input_name()} (opens on dictation)")

    def _open_mic():
        rec.open(lambda: resolve_input_device(cfg["audio"].get("device")))
        print(f"mic opened — capturing from: {_current_input_name()}")

    def _release_mic():
        if rec.is_open and not rec._recording:
            rec.release()
            print("mic released")

    def _watch_input_devices():
        """Follow the system default mic: AirPods connect -> capture from them;
        disconnect -> back to the MacBook mic. Retries while a recording is live."""
        import coreaudio
        try:
            applied = coreaudio.input_signature()
        except Exception:
            return
        while True:
            time.sleep(2)
            try:
                cur = coreaudio.input_signature()
            except Exception:
                continue
            if cur == applied or rec._recording:
                continue
            if not rec.is_open:
                applied = cur  # closed mic binds fresh on the next open
                continue
            try:
                if rec.rebuild(lambda: resolve_input_device(cfg["audio"].get("device"))):
                    applied = cur
                    print(f"input device changed — now capturing from: {_current_input_name()}")
            except Exception as e:
                print(f"input switch failed ({e}); retrying")
    threading.Thread(target=_watch_input_devices, daemon=True).start()

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
            import copy
            before = {k: copy.deepcopy(cfg.get(k)) for k in RESTART_KEYS}
            for k, v in partial.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    for k2, v2 in v.items():
                        if isinstance(v2, dict) and isinstance(cfg[k].get(k2), dict):
                            cfg[k][k2].update(v2)
                        else:
                            cfg[k][k2] = v2
                else:
                    cfg[k] = v
            # restart only when a restart-key actually changed after the merge
            needs_restart = any(cfg.get(k) != before[k] for k in RESTART_KEYS)
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
        def reload_cleaner():
            state["cleaner"] = Cleaner(cfg["llm"])

        @staticmethod
        def restart():
            threading.Timer(0.3, lambda: AppHelper.callAfter(restart_app)).start()

    Controller.cfg = cfg
    dashboard = Dashboard(Controller)
    overlay.set_on_click(dashboard.open)
    # debug/automation hook: open the dashboard at launch if the flag file exists.
    # Also open on a fresh install (onboarding not completed) so the welcome
    # page greets the user on first run.
    from window import onboarded_flag
    _flag = Path.home() / "Library" / "Application Support" / "SayDo" / ".open-dashboard"
    if _flag.exists() or not onboarded_flag().exists():
        _flag.unlink(missing_ok=True)
        AppHelper.callAfter(dashboard.open)
    menu_target = MenuTarget.alloc().initWithDashboard_(dashboard)

    # menu bar icon so SayDo is visible/controllable like a normal app
    status = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
    status.button().setTitle_("🎙️")
    menu = NSMenu.alloc().init()
    open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open SayDo", "openDashboard:", "o")
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
        state["front_app"] = appmodes.frontmost_app_name()
        if cfg["ui"].get("mute_music"):
            threading.Thread(target=ducker.pause, daemon=True).start()
        play_sound("Pop")
        try:
            if not rec.is_open:
                _open_mic()
        except Exception as e:
            print(f"mic open failed: {e}")
            return
        rec.start()
        AppHelper.callAfter(overlay.show)

    def process(audio):
        t0 = time.time()
        app_name = state.get("front_app")
        # read rules live from cfg so saved settings apply without a restart
        mode = appmodes.resolve_mode(
            app_name, (cfg.get("app_modes") or {}).get("rules") or [])
        rms = float((audio ** 2).mean()) ** 0.5 if audio.size else 0.0
        raw = stt.transcribe(audio)
        if not raw:
            print(f"(no speech detected — {audio.size} samples, rms {rms:.6f})")
            return
        text = dictionary.apply_corrections(raw)
        if mode == "standard":
            text = state["cleaner"].clean(text)
        if mode != "raw":
            text = snippets.apply(text)
        if mode == "code":
            text = appmodes.code_postprocess(text)
        inject.inject(text, cfg["inject"])
        history.append(text, audio.size / cfg["audio"]["sample_rate"], app=app_name)
        tag = f"  [{mode} · {app_name}]" if mode != "standard" else ""
        print(f'→ "{text}"{tag}  ({time.time() - t0:.2f}s)')

    def on_release():
        audio = rec.stop()
        play_sound("Bottle")
        if cfg["ui"].get("mute_music"):
            threading.Thread(target=ducker.resume, daemon=True).start()
        AppHelper.callAfter(overlay.hide)
        threading.Thread(target=process, args=(audio,), daemon=True).start()
        # release immediately so the macOS mic-in-use pill clears right away
        threading.Thread(target=_release_mic, daemon=True).start()

    PushToTalk(key, on_press, on_release, mode=mode).start()
    action = "Press" if mode == "toggle" else "Hold"
    print(f"{APP_NAME} ready. {action} [{key}] to dictate ({mode} mode).")
    try:
        AppHelper.runEventLoop()
    finally:
        rec.close()


if __name__ == "__main__":
    main()
