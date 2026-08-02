"""Dashboard window: WKWebView hosting ui/index.html, with a JS bridge.

The page calls window.webkit.messageHandlers.vb.postMessage({id, op, data})
and receives replies via window.__vbDeliver(id, payload). All handlers run on
the main thread (WebKit delivers script messages there).
"""
import json

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
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

import dictionary
import history
import launch_login
import scratchpad
import snippets
from bundle import resource_dir

APP_VERSION = "2.0"


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
        try:
            result = self._dispatch(op, data)
        except Exception as e:  # surface errors to the UI instead of dying
            result = {"error": str(e)}
        if mid is not None and self.webview is not None:
            js = f"window.__vbDeliver({json.dumps(mid)}, {json.dumps(result)})"
            self.webview.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def _dispatch(self, op, d):
        c = self.controller
        if op == "init":
            return {
                "version": APP_VERSION,
                "config": c.cfg,
                "status": c.status(),
                "history": list(reversed(history.read_all())),
                "stats": history.stats(),
                "dictionary": dictionary.entries(),
                "most_corrected": dictionary.most_corrected(),
                "snippets": snippets.entries(),
                "notes": scratchpad.entries(),
                "launch_login": launch_login.enabled(),
            }
        if op == "status":
            return c.status()
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
        self.window.setTitle_("VoiceBud")
        self.window.setTitlebarAppearsTransparent_(True)
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
    """Target object for the status-bar menu's 'Open VoiceBud' item."""

    def initWithDashboard_(self, dashboard):
        self = objc.super(MenuTarget, self).init()
        if self is None:
            return None
        self.dashboard = dashboard
        return self

    def openDashboard_(self, _sender):
        self.dashboard.open()


def write_config(cfg, path):
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
