import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import signal
import sys
import threading
import time
import urllib.request

PROJECT = Path(__file__).resolve().parent
if (PROJECT / ".deps").exists():
    sys.path.insert(0, str(PROJECT / ".deps"))

from app.core import APP_ID, PAYOUT, TARGET, VERSION, atomic_json, partition, point_address
from app.service import Application
from app.server import make_server
from app.tray import Tray
from app.window import open_window, run_window
from app.runtime import InstanceLock, default_data_directory, fallback_data_directory


def reopen_existing(directory, no_browser):
    """The lock owner may still be writing its URL during a concurrent launch."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    from app.window import valid_local_url
    for _ in range(40):
        try:
            existing = json.loads((directory / "instance.json").read_text(encoding="utf8"))
            if existing.get("app") != APP_ID or not valid_local_url(existing.get("url")):
                raise ValueError("Invalid existing instance URL")
            with opener.open(existing["url"] + "/api/ping", timeout=.5) as response:
                if json.load(response).get("app") != APP_ID:
                    raise ValueError("Existing endpoint is not KeyTrail")
            if not no_browser:
                open_window(existing["url"], directory)
            if sys.stdout:
                print("KEYTRAIL_ALREADY_RUNNING " + existing["url"], flush=True)
            return
        except (OSError, ValueError, KeyError):
            time.sleep(.1)
    raise RuntimeError("The data directory is locked by another instance; its local UI is not responding")


def main():
    if os.name != "nt":
        os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--ui-url", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--self-test-cuda", action="store_true")
    parser.add_argument("--self-test-payout", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.ui_url:
        if not args.data_dir:
            raise ValueError("UI data directory required")
        try:
            run_window(args.ui_url, args.data_dir)
        except Exception:
            if os.name == "nt":
                ctypes.windll.user32.MessageBoxW(None,
                    "KeyTrail could not open its embedded window. Check that Microsoft Edge WebView2 Runtime and .NET Framework are installed. Details are saved in Puzzle71Desk-error.log.",
                    "KeyTrail", 0x10)
            raise
        return
    directory = Path(args.data_dir).expanduser() if args.data_dir else default_data_directory(PROJECT)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / (".write-check-" + str(os.getpid()))
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        if args.data_dir:
            raise
        directory = fallback_data_directory()
        directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    lock = InstanceLock(directory)
    if not lock.acquire():
        reopen_existing(directory, args.no_browser)
        return
    try:
        run_backend(args, directory)
    finally:
        lock.close()


def run_backend(args, directory):
    if args.diagnostics or args.self_test_cuda or args.self_test_payout:
        from app.engine import detect_gpu
        report = {"pass": True, "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                  "platform": platform.system(), "architecture": platform.machine(),
                  "ui": "native-webview2" if os.name == "nt" else "local-browser",
                  "result_protection": "Windows DPAPI" if os.name == "nt" else "AES-256-GCM + owner-only local key file",
                  "default_payout_empty": PAYOUT == "",
                  "curve_check": point_address(1) == "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH",
                  "partition_check": partition(2, 1)[1] + 1 == partition(2, 2)[0],
                  "gpu": detect_gpu(), "target": TARGET, "payout": PAYOUT,
                  "automatic_broadcast": False}
        assert report["default_payout_empty"] and report["curve_check"] and report["partition_check"]
        if args.self_test_payout:
            from app.selftest import payout_selftest
            report["offline_payout_selftest"]=payout_selftest(directory/("offline-payout-"+str(time.time_ns())))
        if args.self_test_cuda:
            from app.engine import gpu_selftest
            if not report["gpu"].get("available"):
                raise RuntimeError("未找到兼容 NVIDIA CUDA 设备")
            def test_log(line):
                with (directory / "cuda-selftest.log").open("a", encoding="utf8") as file:
                    file.write(line + "\n")
            report["cuda_selftest"] = gpu_selftest(directory / "selftest", threading.Event(),
                test_log)
        atomic_json(directory / "diagnostics.json", report)
        if sys.stdout:
            print(json.dumps(report, indent=2))
        return
    application = Application(directory)
    try:
        server = make_server(application, args.port)
    except BaseException:
        application.shutdown()
        raise
    url = "http://127.0.0.1:" + str(server.server_address[1])
    atomic_json(directory / "instance.json", {"url": url, "pid": os.getpid(), "app": APP_ID})
    tray = Tray(lambda: open_window(url, directory), application.stop,
                lambda: (application.shutdown(), server.shutdown()),
                lambda: application.settings.get("language", "en"))
    prior_signals = {}

    def shutdown_signal(signum, frame):
        application.stop("Process interrupted; saving confirmed progress")
        # BaseServer.shutdown must run on a different thread from serve_forever.
        threading.Thread(target=server.shutdown, name="signal-shutdown", daemon=True).start()

    try:
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGTERM", "SIGINT"):
                sig = getattr(signal, name)
                prior_signals[sig] = signal.signal(sig, shutdown_signal)
        if not args.no_tray:
            tray.start()
        if not args.no_browser:
            open_window(url, directory)
        if sys.stdout:
            print("PUZZLE71_DESK_READY " + url, flush=True)
            if os.name != "nt":
                print("Local browser UI. Use Quit in the page or Ctrl+C to stop and save.", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            application.shutdown()
            server.server_close()
            tray.close()
        finally:
            for sig, handler in prior_signals.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        error = traceback.format_exc()
        location = Path(sys.executable).parent if getattr(sys, "frozen", False) else PROJECT
        try:
            (location / "Puzzle71Desk-error.log").write_text(error, encoding="utf8")
        except OSError:
            fallback = fallback_data_directory()
            fallback.mkdir(parents=True, exist_ok=True)
            (fallback / "Puzzle71Desk-error.log").write_text(error, encoding="utf8")
        if sys.stderr:
            print(error, file=sys.stderr)
        elif os.name == "nt" and not any(flag in sys.argv for flag in
                ("--diagnostics", "--self-test-cuda", "--self-test-payout", "--no-browser")):
            ctypes.windll.user32.MessageBoxW(None, "启动遇到问题，请查看软件旁的 Puzzle71Desk-error.log。",
                                            "71 号工作台", 0x10)
        sys.exit(1)
