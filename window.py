"""Dashboard window: WKWebView hosting ui/index.html, with a JS bridge.

The page calls window.webkit.messageHandlers.vb.postMessage({id, op, data})
and receives replies via window.__vbDeliver(id, payload). All handlers run on
the main thread (WebKit delivers script messages there).
"""
import json
import threading
from pathlib import Path

import objc
import yaml
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSURL
from PyObjCTools import AppHelper
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

import dictionary
import history
import launch_login
import ollama_setup
import scratchpad
import snippets
from bundle import resource_dir

APP_VERSION = "2.3"

RELEASES_API = "https://api.github.com/repos/nikooo11/SayDo/releases/latest"

_SETTINGS_PANES = {
    "input": "Privacy_ListenEvent",
    "accessibility": "Privacy_Accessibility",
    "mic": "Privacy_Microphone",
}


def onboarded_flag():
    return Path.home() / "Library" / "Application Support" / "SayDo" / ".onboarded"


def permissions_status():
    """Live snapshot of the three permissions the welcome page tracks."""
    try:
        import Quartz
        input_monitoring = bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        input_monitoring = True
    try:
        from ApplicationServices import AXIsProcessTrusted
        accessibility = bool(AXIsProcessTrusted())
    except Exception:
        accessibility = True
    try:
        import AVFoundation
        st = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_("soun")
        # AVAuthorizationStatus: 0 not determined, 1 restricted, 2 denied, 3 authorized
        mic = {0: "undetermined", 1: "denied", 2: "denied", 3: "granted"}.get(st, "unknown")
    except Exception:
        mic = "unknown"
    return {"input_monitoring": input_monitoring, "accessibility": accessibility, "mic": mic}


def _version_tuple(s):
    return tuple(int(p) for p in str(s).strip().lstrip("v").split("."))


def is_newer(latest, current):
    """True if version string `latest` > `current`; malformed input = no update."""
    try:
        return _version_tuple(latest) > _version_tuple(current)
    except (ValueError, AttributeError):
        return False


def check_update():
    """Blocking GitHub release lookup; call from a worker thread only."""
    try:
        import requests
        info = requests.get(RELEASES_API, timeout=4).json()
        tag = (info.get("tag_name") or "").lstrip("v")
        if is_newer(tag, APP_VERSION):
            return {"update": {"version": tag, "url": info.get("html_url")}}
    except Exception:
        pass
    return {"update": None}


class _Bridge(NSObject):
    def initWithController_(self, controller):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self.controller = controller
        self.webview = None
        return self

    def userContentController_didReceiveScriptMessage_(self, _ucc, msg):
        body = msg.body()
        mid, op = body.get("id"), body.get("op")
        data = body.get("data") or {}
        if op == "update.check":
            # network call: run off the main thread, deliver the reply later
            threading.Thread(target=self._update_worker, args=(mid,), daemon=True).start()
            return
        try:
            result = self._dispatch(op, data)
        except Exception as e:  # surface errors to the UI instead of dying
            result = {"error": str(e)}
        self._deliver(mid, result)

    @objc.python_method
    def _deliver(self, mid, result):
        """Send a reply to the page. Main thread only (evaluateJavaScript)."""
        if mid is None or self.webview is None:
            return
        js = f"window.__vbDeliver({json.dumps(mid)}, {json.dumps(result)})"
        self.webview.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def _update_worker(self, mid):
        result = check_update()
        AppHelper.callAfter(self._deliver, mid, result)

    @objc.python_method
    def _dispatch(self, op, d):
        c = self.controller
        if op == "init":
            try:
                from Foundation import NSFullUserName
                parts = str(NSFullUserName()).split()
                first_name = parts[0] if parts else ""
                initials = "".join(p[0] for p in parts[:2]).upper()
            except Exception:
                first_name, initials = "", ""
            return {
                "version": APP_VERSION,
                "user": first_name,
                "initials": initials,
                "config": c.cfg,
                "status": c.status(),
                "history": list(reversed(history.read_all())),
                "stats": history.stats(),
                "dictionary": dictionary.entries(),
                "most_corrected": dictionary.most_corrected(),
                "snippets": snippets.entries(),
                "notes": scratchpad.entries(),
                "launch_login": launch_login.enabled(),
                "onboarded": onboarded_flag().exists(),
            }
        if op == "status":
            return c.status()
        if op == "permissions":
            return permissions_status()
        if op == "onboarded":
            flag = onboarded_flag()
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
            return {"ok": True}
        if op == "open.settings":
            anchor = _SETTINGS_PANES.get(d.get("pane"))
            if not anchor:
                return {"error": f"unknown pane {d.get('pane')}"}
            from AppKit import NSWorkspace
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(
                f"x-apple.systempreferences:com.apple.preference.security?{anchor}"))
            return {"ok": True}
        if op == "open.url":
            url = d.get("url") or ""
            if not url.startswith("https://github.com/"):
                return {"error": "blocked url"}
            from AppKit import NSWorkspace
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
            return {"ok": True}
        if op == "audio.devices":
            return {"devices": _input_devices()}
        if op == "refresh":
            return {"history": list(reversed(history.read_all())),
                    "stats": history.stats(),
                    "most_corrected": dictionary.most_corrected()}
        if op == "copy":
            import inject
            inject._set_clipboard(d["text"])
            return {"ok": True}
        if op == "history.clear":
            history.clear()
            return {"ok": True}
        if op == "dict.add":
            dictionary.add(d["word"], d.get("misspelling"))
            return {"dictionary": dictionary.entries()}
        if op == "dict.remove":
            dictionary.remove(d["word"])
            return {"dictionary": dictionary.entries()}
        if op == "snip.add":
            snippets.add(d["trigger"], d["expansion"])
            return {"snippets": snippets.entries()}
        if op == "snip.remove":
            snippets.remove(d["trigger"])
            return {"snippets": snippets.entries()}
        if op == "note.save":
            nid = scratchpad.save(d.get("id"), d.get("title", ""), d.get("body", ""))
            return {"id": nid, "notes": scratchpad.entries()}
        if op == "note.delete":
            scratchpad.delete(d["id"])
            return {"notes": scratchpad.entries()}
        if op == "cleanup.state":
            llm = self.controller.cfg.get("llm") or {}
            base = (llm.get("base_url") or "http://localhost:11434").rstrip("/")
            return {
                **ollama_setup.state(base, llm.get("model", "qwen3:4b-instruct")),
                "api_key_set": bool((llm.get("api") or {}).get("api_key")),
                "backend": self.controller.status().get("cleanup", "off"),
                "setup": ollama_setup.progress(),
            }
        if op == "ollama.setup":
            llm = self.controller.cfg.get("llm") or {}
            base = (llm.get("base_url") or "http://localhost:11434").rstrip("/")
            started = ollama_setup.start(
                base, llm.get("model", "qwen3:4b-instruct"),
                on_done=self.controller.reload_cleaner)
            return {"started": started}
        if op == "ollama.progress":
            return ollama_setup.progress()
        if op == "settings.save":
            return c.save_settings(d)
        if op == "restart":
            c.restart()
            return {"ok": True}
        return {"error": f"unknown op {op}"}


def _input_devices():
    """Input devices for the settings mic picker, deduped by name."""
    try:
        import sounddevice as sd
        default_idx = sd.default.device[0]
        seen, out = set(), []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0 or d["name"] in seen:
                continue
            seen.add(d["name"])
            out.append({"index": i, "name": d["name"], "default": i == default_idx})
        return out
    except Exception:
        return []


class Dashboard:
    def __init__(self, controller):
        self.controller = controller
        self.window = None
        self.bridge = None

    def open(self):
        if self.window is None:
            self._build()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def _build(self):
        ucc = WKUserContentController.alloc().init()
        self.bridge = _Bridge.alloc().initWithController_(self.controller)
        ucc.addScriptMessageHandler_name_(self.bridge, "vb")
        wkcfg = WKWebViewConfiguration.alloc().init()
        wkcfg.setUserContentController_(ucc)

        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((160, 120), (1080, 700)), style, NSBackingStoreBuffered, False)
        self.window.setTitle_("SayDo")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(1)  # hidden: no text in the bar
        from AppKit import NSColor
        # match the page background so the titlebar merges with the content
        self.window.setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.051, 0.063, 0.086, 1.0))
        self.window.setMinSize_((860, 560))
        self.window.setReleasedWhenClosed_(False)

        webview = WKWebView.alloc().initWithFrame_configuration_(
            ((0, 0), (1080, 700)), wkcfg)
        webview.setAutoresizingMask_(18)  # width + height resizable
        self.bridge.webview = webview
        index = resource_dir() / "ui" / "index.html"
        webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(str(index)),
            NSURL.fileURLWithPath_(str(resource_dir() / "ui")))
        self.window.setContentView_(webview)


class MenuTarget(NSObject):
    """Target object for the status-bar menu's 'Open SayDo' item."""

    def initWithDashboard_(self, dashboard):
        self = objc.super(MenuTarget, self).init()
        if self is None:
            return None
        self.dashboard = dashboard
        return self

    def openDashboard_(self, _sender):
        self.dashboard.open()


class AppDelegate(NSObject):
    """Regular-app behavior: clicking the Dock icon reopens the dashboard."""

    def initWithDashboard_(self, dashboard):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.dashboard = dashboard
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, has_windows):
        if not has_windows:
            self.dashboard.open()
        return True


def write_config(cfg, path):
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
