"""Frameless Windows UI, isolated from the computing and payout backend."""
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

from .core import APP_ID, ROOT, atomic_json


def valid_local_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "http" and parts.hostname == "127.0.0.1"
                and parts.port is not None and parts.username is None
                and parts.password is None and parts.path in ("", "/")
                and not parts.query and not parts.fragment)
    except (ValueError, AttributeError):
        return False


def restore_existing(directory, url):
    """Validate the recorded owner before touching an existing UI window."""
    try:
        data = json.loads((Path(directory) / "ui-instance.json").read_text())
        if data["app"] != APP_ID or data["url"] != url:
            return False
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.IsWindow.argtypes = [w.HWND]
        user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [w.HWND]
        hwnd, pid = int(data["hwnd"]), w.DWORD()
        if not user.IsWindow(hwnd):
            return False
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        text = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, text, 256)
        if pid.value != data["pid"] or not text.value.startswith("KeyTrail"):
            return False
        user.ShowWindow(hwnd, 9)
        user.SetForegroundWindow(hwnd)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def open_window(url, directory):
    if not valid_local_url(url):
        raise ValueError("Expected a local KeyTrail URL")
    if os.name != "nt":
        # Browser mode has no WebView2/.NET/GTK dependency. The backend remains
        # loopback-only; a server can use --no-browser and an SSH local tunnel.
        return webbrowser.open(url, new=2)
    if os.name == "nt" and restore_existing(directory, url):
        return
    command = ([sys.executable] if getattr(sys, "frozen", False)
               else [sys.executable, str(ROOT / "main.py")])
    command += ["--ui-url", url, "--data-dir", str(directory)]
    subprocess.Popen(command, creationflags=0x08000000 if os.name == "nt" else 0)


class WindowControls:
    def __init__(self):
        self._window = None
        self._maximized = False

    def minimize(self):
        self._window.minimize()

    def maximize(self):
        if self._maximized:
            self._maximized = False
            self._window.restore()
        else:
            self._maximized = True
            self._window.maximize()
        return self._maximized

    def close(self):
        # Same behavior as the previous browser close: backend stays in tray.
        self._window.hide()


def run_window(url, directory):
    if os.name != "nt" or not valid_local_url(url):
        raise ValueError("The native UI requires Windows and a local URL")
    directory = Path(directory).resolve()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    name = "Local\\KeyTrailUI-" + hashlib.sha256(str(directory).encode()).hexdigest()
    mutex = None
    for _ in range(50):
        ctypes.set_last_error(0)
        mutex = kernel.CreateMutexW(None, False, name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() != 183:
            break
        kernel.CloseHandle(mutex)
        mutex = None
        if restore_existing(directory, url):
            return
        time.sleep(.2)
    if mutex is None:
        raise RuntimeError("The existing UI is still shutting down")
    try:
        import webview
        controls, allow_exit = WindowControls(), threading.Event()
        user = ctypes.WinDLL("user32", use_last_error=True)
        work = w.RECT()
        user.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)
        width = min(1320, max(760, work.right - work.left - 48))
        height = min(900, max(600, work.bottom - work.top - 64))
        webview.settings.update(ALLOW_DOWNLOADS=True,
                                DRAG_REGION_DIRECT_TARGET_ONLY=True,
                                OPEN_EXTERNAL_LINKS_IN_BROWSER=True)
        window = webview.create_window("KeyTrail", url, width=width, height=height,
            min_size=(600, 500), frameless=True, easy_drag=False, shadow=True,
            background_color="#252b2e", text_select=True, js_api=controls)
        controls._window = window

        def shown():
            hwnd = int(window.native.Handle.ToInt64())
            atomic_json(directory / "ui-instance.json", {
                "app": APP_ID, "url": url, "pid": os.getpid(), "hwnd": hwnd})

        def closing():
            if allow_exit.is_set():
                return True
            window.hide()
            return False

        def watch_backend():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            failures = 0
            while not allow_exit.wait(2):
                try:
                    with opener.open(url + "/api/bootstrap", timeout=2) as response:
                        response.read(1)
                    failures = 0
                except OSError:
                    failures += 1
                    if failures >= 3:
                        allow_exit.set()
                        window.destroy()
                        return

        window.events.shown += shown
        window.events.closing += closing
        window.events.maximized += lambda: setattr(controls, "_maximized", True)
        window.events.restored += lambda: setattr(controls, "_maximized", False)
        webview.start(watch_backend, gui="edgechromium", private_mode=True,
                      storage_path=str(directory / "webview-cache"),
                      icon=str(ROOT / "web/icon.ico"))
    finally:
        kernel.CloseHandle(mutex)
