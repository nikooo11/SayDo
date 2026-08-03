"""Guided local-cleanup setup: install Ollama and pull the model, with progress.

Runs in a background thread; the UI polls progress(). Phases:
idle -> downloading (Ollama.app) -> installing -> starting -> pulling (model)
-> done | error
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import requests

OLLAMA_ZIP = "https://ollama.com/download/Ollama-darwin.zip"
APP = Path("/Applications/Ollama.app")

_CLI_CANDIDATES = (
    APP / "Contents" / "Resources" / "ollama",
    Path("/opt/homebrew/bin/ollama"),
    Path("/usr/local/bin/ollama"),
)
_server_proc = None


def cli_path():
    p = shutil.which("ollama")
    if p:
        return Path(p)
    for c in _CLI_CANDIDATES:
        if c.exists():
            return c
    return None


def _ping(base_url):
    try:
        requests.get(f"{base_url}/api/tags", timeout=1.5)
        return True
    except requests.RequestException:
        return False


def ensure_server(base_url, wait_s=25):
    """Make sure an Ollama server answers at base_url. If none is running,
    spawn the bundled CLI headlessly (`ollama serve`) — no menu bar app
    needed. Returns True once the server responds."""
    global _server_proc
    if _ping(base_url):
        return True
    cli = cli_path()
    if cli is None:
        return False
    if _server_proc is None or _server_proc.poll() is not None:
        # Pin the universal binary to arm64: a server that ends up under
        # Rosetta (e.g. relaunched by a translated updater) spawns Intel
        # runners — no Metal GPU, ~2 tok/s cleanup, and macOS "Support
        # Ending for Intel-based Apps" popups.
        cmd = [str(cli), "serve"]
        import platform
        if platform.machine() == "arm64":
            cmd = ["/usr/bin/arch", "-arm64"] + cmd
        _server_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(wait_s * 2):
        if _ping(base_url):
            return True
        time.sleep(0.5)
    return False


def stop_server():
    """Terminate the server we spawned (never touches a desktop-app server)."""
    global _server_proc
    if _server_proc is not None and _server_proc.poll() is None:
        _server_proc.terminate()
    _server_proc = None

_progress = {"phase": "idle", "detail": "", "pct": 0}
_lock = threading.Lock()
_thread = None


def _set(phase, detail="", pct=0):
    with _lock:
        _progress.update(phase=phase, detail=detail, pct=pct)


def progress():
    with _lock:
        return dict(_progress)


def state(base_url, model):
    """What's already in place: app installed, server running, model pulled."""
    installed = APP.exists() or shutil.which("ollama") is not None
    running, has_model = False, False
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=2)
        running = True
        names = [m["name"] for m in r.json().get("models", [])]
        has_model = any(n == model or n.split(":")[0] == model for n in names)
    except requests.RequestException:
        pass
    return {"installed": installed, "running": running, "model": has_model}


def start(base_url, model, on_done=None):
    """Kick off setup in the background. Returns False if already running."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return False
    _set("starting", "Checking what's needed…", 0)
    _thread = threading.Thread(target=_run, args=(base_url, model, on_done), daemon=True)
    _thread.start()
    return True


def _run(base_url, model, on_done):
    try:
        if not state(base_url, model)["installed"]:
            _download_app()
        if not state(base_url, model)["running"]:
            _set("starting", "Starting Ollama…", 0)
            if not ensure_server(base_url, wait_s=60):
                raise RuntimeError("Ollama did not start — open Ollama.app once manually")
        if not state(base_url, model)["model"]:
            _pull(base_url, model)
        _set("done", "Local cleanup ready", 100)
        if on_done:
            on_done()
    except Exception as e:
        _set("error", str(e), 0)


def _download_app():
    _set("downloading", "Downloading Ollama…", 0)
    with requests.get(OLLAMA_ZIP, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            tmp = f.name
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total:
                    _set("downloading", "Downloading Ollama…", int(got / total * 100))
    _set("installing", "Installing Ollama…", 0)
    # ditto preserves the .app bundle structure and signatures
    subprocess.run(["ditto", "-x", "-k", tmp, "/Applications"], check=True)
    Path(tmp).unlink(missing_ok=True)


def _pull(base_url, model):
    _set("pulling", f"Downloading {model}…", 0)
    with requests.post(f"{base_url}/api/pull", json={"name": model, "stream": True},
                       stream=True, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("error"):
                raise RuntimeError(d["error"])
            total, done = d.get("total"), d.get("completed")
            if total and done:
                _set("pulling", f"Downloading {model}…", int(done / total * 100))
