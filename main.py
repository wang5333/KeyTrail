import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

PROJECT = Path(__file__).resolve().parent
if (PROJECT / ".deps").exists():
    sys.path.insert(0, str(PROJECT / ".deps"))

from app.core import APP_ID, PAYOUT, ROOT, TARGET, VERSION, atomic_json, partition, point_address, validate_payout
from app.service import Application
from app.server import make_server
from app.tray import Tray
from app.window import open_window, run_window


def main():
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
    if args.ui_url:
        if not args.data_dir:
            raise ValueError("UI data directory required")
        try:
            run_window(args.ui_url, args.data_dir)
        except Exception:
            ctypes.windll.user32.MessageBoxW(None,
                "KeyTrail could not open its embedded window. Check that Microsoft Edge WebView2 Runtime and .NET Framework are installed. Details are saved in Puzzle71Desk-error.log.",
                "KeyTrail", 0x10)
            raise
        return
    directory = Path(args.data_dir) if args.data_dir else (
        Path(sys.executable).parent / "Puzzle71Desk-data" if getattr(sys, "frozen", False) else PROJECT / ".local-data")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / (".write-check-" + str(os.getpid()))
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        if args.data_dir:
            raise
        directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Puzzle71Desk"
        directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    if args.diagnostics or args.self_test_cuda or args.self_test_payout:
        from app.engine import detect_gpu
        report = {"pass": True, "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
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
    lock_file = (directory / "instance.lock").open("a+b")
    if os.name == "nt":
        import msvcrt
        lock_file.seek(0)
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if not args.no_browser:
                existing = json.loads((directory / "instance.json").read_text(encoding="utf8"))
                if existing.get("app") != APP_ID or not existing["url"].startswith("http://127.0.0.1:"):
                    raise ValueError("现有窗口信息错误")
                open_window(existing["url"], directory)
            lock_file.close()
            return
    application = Application(directory)
    server = make_server(application, args.port)
    url = "http://127.0.0.1:" + str(server.server_address[1])
    atomic_json(directory / "instance.json", {"url": url, "pid": os.getpid(), "app": APP_ID})
    tray = Tray(lambda: open_window(url, directory), application.stop,
                lambda: (application.shutdown(), server.shutdown()),
                lambda: application.settings.get("language", "en"))
    if not args.no_tray:
        tray.start()
    if not args.no_browser:
        open_window(url, directory)
    if sys.stdout:
        print("PUZZLE71_DESK_READY " + url, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        application.shutdown()
        server.server_close()
        tray.close()
        lock_file.close()


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
            fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Puzzle71Desk"
            fallback.mkdir(parents=True, exist_ok=True)
            (fallback / "Puzzle71Desk-error.log").write_text(error, encoding="utf8")
        if sys.stderr:
            print(error, file=sys.stderr)
        elif os.name == "nt" and not any(flag in sys.argv for flag in
                ("--diagnostics", "--self-test-cuda", "--self-test-payout", "--no-browser")):
            ctypes.windll.user32.MessageBoxW(None, "启动遇到问题，请查看软件旁的 Puzzle71Desk-error.log。",
                                            "71 号工作台", 0x10)
        sys.exit(1)
